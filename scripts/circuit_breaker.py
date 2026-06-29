#!/usr/bin/env python3
"""Hardcoded portfolio circuit breaker — runs OUTSIDE the agent loop.

A dumb, deterministic safety net: if live portfolio drawdown breaches a hard
limit, it force-liquidates every open position and trips a persistent HALT flag
that all agents honor — regardless of what the LLMs decide. Run it on a tight
intraday cron (e.g. every 1-2 minutes) independent of the main pipeline.

    # crontab — every 2 minutes during market hours
    */2 9-15 * * 1-5  cd ~/AutoTrader && python3 scripts/circuit_breaker.py >> logs/circuit.log 2>&1

Drawdown = (current equity - day_start_equity) / total_capital. The threshold
is config/trading_policy.yaml: max_daily_loss_pct, with a hard floor here so a
mis-set config can't disable the breaker.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env, override=True)
    except ImportError:
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

import structlog
from autotrader.core.config import load_config
from autotrader.safety.controls import _halt_file_active, trip_halt
from autotrader.core.session_store import load_session
from autotrader.tools.price_utils import live_ltp
from autotrader.tools.notifications import get_notifier

logger = structlog.get_logger()

# Hard floor: even if config says a larger number, never let the breaker exceed this.
HARD_MAX_DRAWDOWN_PCT = 5.0


def _portfolio_drawdown(positions: list[dict], capital: float) -> tuple[float, float]:
    """Return (unrealized_pnl, drawdown_pct) using live prices."""
    total_pnl = 0.0
    for p in positions:
        if p.get("status") not in ("OPEN", None):
            continue
        sym = p.get("symbol")
        qty = p.get("qty", 0)
        entry = p.get("entry_price") or p.get("assumed_entry") or 0
        px = live_ltp(sym) if sym else None
        if px and entry and qty:
            total_pnl += (px - entry) * qty
    dd_pct = (-total_pnl / capital * 100) if (capital and total_pnl < 0) else 0.0
    return round(total_pnl, 2), round(dd_pct, 3)


def _force_liquidate(positions: list[dict], dry_run: bool) -> int:
    if dry_run:
        logger.warning("circuit_breaker_dry_run_no_real_liquidation")
        return 0
    from autotrader.tools.broker_tools import get_broker, ORDER_TYPE_MARKET
    cfg = load_config()
    broker = get_broker(cfg.broker)
    closed = 0
    for p in positions:
        if p.get("status") not in ("OPEN", None):
            continue
        try:
            broker.place_order(symbol=p["symbol"], qty=p.get("qty", 0), side="SELL",
                               order_type=ORDER_TYPE_MARKET, tag=f"HALT-{p.get('symbol')}")
            closed += 1
        except Exception as exc:
            logger.error("force_liquidate_failed", symbol=p.get("symbol"), error=str(exc))
    return closed


def main():
    if _halt_file_active():
        logger.info("circuit_breaker_already_halted")
        return

    cfg = load_config()
    capital = cfg.trading_policy.total_capital
    threshold = min(cfg.trading_policy.max_daily_loss_pct, HARD_MAX_DRAWDOWN_PCT)

    session = load_session() or {}
    positions = session.get("positions", [])
    dry_run = session.get("dry_run", cfg.trading_policy.dry_run)

    if not positions:
        logger.info("circuit_breaker_no_positions")
        return

    pnl, dd_pct = _portfolio_drawdown(positions, capital)
    logger.info("circuit_breaker_check", unrealized_pnl=pnl, drawdown_pct=dd_pct, threshold=threshold)

    if dd_pct >= threshold:
        reason = f"Portfolio drawdown {dd_pct:.2f}% >= {threshold:.2f}% (unrealized ₹{pnl:,.0f})"
        closed = _force_liquidate(positions, dry_run)
        trip_halt(reason)
        try:
            get_notifier(cfg.notifications).send(
                subject="🛑 CIRCUIT BREAKER TRIPPED",
                body=(f"{reason}\n"
                      f"Force-liquidated {closed} position(s).\n"
                      f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n"
                      "Trading HALTED. Clear with: python3 scripts/clear_halt.py"),
            )
        except Exception as exc:
            logger.warning("circuit_breaker_notify_failed", error=str(exc))
        print(f"HALT TRIPPED: {reason}")
    else:
        print(f"OK — drawdown {dd_pct:.2f}% < {threshold:.2f}%")


if __name__ == "__main__":
    main()

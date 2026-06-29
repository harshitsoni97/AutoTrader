"""Dry-Run P&L Agent — computes assumed end-of-day P&L for simulated positions.

For each dry-run position we fetch the actual EOD closing price and simulate
what would have happened:
  - EOD >= target2        → full exit at target2 (both targets hit)
  - target1 <= EOD < target2 → partial exit at target1 (half qty), rest at EOD
  - stop < EOD < target1  → still open at EOD — unrealized P&L at close
  - EOD <= stop           → stopped out at stop

This gives a realistic "what would we have made" number without live order tracking.
"""

from __future__ import annotations

import structlog
import math
from datetime import date, timedelta
from typing import Any

from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "DryRunPnLAgent"


def _eod_price(symbol: str) -> float | None:
    """Today's closing price via the shared Upstox price source.

    Same helper the compete leaderboard uses, so assumed P&L and the leaderboard
    always reconcile. require_today=False here: dry-run P&L only runs post-market
    on actual trading days, and if LTP is briefly unavailable we still want the
    latest settled close rather than a hard None.
    """
    from autotrader.tools.price_utils import closing_price
    return closing_price(symbol, require_today=False)


def _day_ohlc(symbol: str) -> dict | None:
    """Trade-day OHLC for a symbol via the Upstox daily candle.

    Returns {open, high, low, close, date} or None. Used to (a) decide whether a
    BUY-LIMIT actually filled, and (b) judge stop/target from intraday high/low.
    """
    try:
        from autotrader.tools.price_utils import _instrument_key
        from autotrader.tools import upstox_data
        ikey = _instrument_key(symbol)
        if not ikey:
            return None
        today = date.today().isoformat()
        frm = (date.today() - timedelta(days=4)).isoformat()
        to = (date.today() + timedelta(days=1)).isoformat()
        # Aggregate TODAY's 30-min candles. The daily ("days") candle for the
        # current session isn't published intraday/just-after-close — it returns
        # the last settled day, so using it silently scores against stale data.
        rows = upstox_data.get_historical_candles(ikey, "minutes", 30, frm, to)
        todays = [r for r in (rows or []) if str(r.get("timestamp", "")).startswith(today)]
        if not todays:
            logger.warning("no_intraday_candles_today", symbol=symbol)
            return None
        todays.sort(key=lambda r: r.get("timestamp", ""))
        return {
            "open": float(todays[0]["open"]),
            "high": max(float(r["high"]) for r in todays),
            "low": min(float(r["low"]) for r in todays),
            "close": float(todays[-1]["close"]),
            "date": today,
        }
    except Exception as exc:
        logger.warning("day_ohlc_failed", symbol=symbol, error=str(exc))
        return None


def _simulate_pnl(pos: dict, ohlc: dict, half_spread_bps: float = 0.0,
                  impact_bps_per_lakh: float = 0.0) -> dict:
    """Realistic fill + intraday-aware exit simulation for one position.

    Two honesty rules that EOD-close P&L misses:
      1. FILL — a BUY-LIMIT only fills if the day actually traded at/below the
         limit (day_low <= limit). If it gapped open below the limit you fill at
         the open; otherwise at the limit. If the market never reached it →
         'not_filled', zero P&L (it was never a real trade).
      2. EXIT — stop/target are judged from intraday HIGH/LOW, not the close,
         conservatively assuming the stop is hit first when both are in range.
         (Catches stops hit on an opening dip that an EOD-close view hides.)
    """
    from autotrader.core.slippage import slipped_fill
    limit = pos.get("assumed_entry") or pos.get("entry_price") or 0  # the order price
    stop = pos.get("stop", 0)
    target1 = pos.get("target1", 0)
    target2 = pos.get("target2", 0)
    qty = pos.get("qty", 0)

    if not limit or not qty or not ohlc:
        return {"pnl": 0.0, "scenario": "no_data"}

    o, h, l, c = ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"]

    # 1) FILL CHECK — BUY LIMIT fills only if price reached the limit.
    if l > limit:
        return {"pnl": 0.0, "scenario": "not_filled", "eod_price": c, "fill_price": None}
    raw_entry = min(limit, o)  # gap below limit → fill at open, else at limit
    entry, _ = slipped_fill(raw_entry, qty, "BUY", half_spread_bps, impact_bps_per_lakh)

    def _sell(price: float, q: int) -> float:
        fill, _ = slipped_fill(price, q, "SELL", half_spread_bps, impact_bps_per_lakh)
        return fill

    # 2) EXIT from intraday high/low (stop-first when both touched).
    if stop and l <= stop:
        exit_px = _sell(stop, qty)
        pnl = qty * (exit_px - entry)
        scenario = "stopped_out"
    elif target2 and h >= target2:
        exit_px = _sell(target2, qty)
        pnl = qty * (exit_px - entry)
        scenario = "target2_hit"
    elif target1 and h >= target1:
        half = max(1, qty // 2)
        rest = qty - half
        pnl = half * (_sell(target1, half) - entry) + rest * (_sell(c, rest) - entry)
        scenario = "target1_hit_partial"
    else:
        exit_px = _sell(c, qty)
        pnl = qty * (exit_px - entry)
        scenario = "open_at_close"

    return {"pnl": round(pnl, 2), "scenario": scenario, "eod_price": c,
            "fill_price": round(entry, 2)}


def dry_run_pnl_agent(state: TradingState) -> dict[str, Any]:
    logger.info("dry_run_pnl_starting")

    positions = state.get("positions", [])
    dry_run = state.get("dry_run", True)

    if not dry_run:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="skipped_live_mode", data={})]}

    if not positions:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_positions", data={})]}

    try:
        from autotrader.core.config import load_config
        _tp = load_config().trading_policy
        half_spread_bps = getattr(_tp, "dry_run_slippage_bps", 4.0)
        impact_bps_per_lakh = getattr(_tp, "dry_run_impact_bps_per_lakh", 1.5)
    except Exception:
        half_spread_bps, impact_bps_per_lakh = 4.0, 1.5

    outcomes = []
    total_assumed_pnl = 0.0

    for pos in positions:
        symbol = pos.get("symbol", "")
        ohlc = _day_ohlc(symbol)
        if ohlc is None:
            logger.warning("no_day_ohlc", symbol=symbol)
            outcomes.append({
                "symbol": symbol,
                "pnl": 0.0,
                "scenario": "price_unavailable",
                "entry": pos.get("assumed_entry") or pos.get("entry_price"),
                "stop": pos.get("stop"),
                "target1": pos.get("target1"),
                "target2": pos.get("target2"),
                "qty": pos.get("qty"),
            })
            continue

        result = _simulate_pnl(pos, ohlc, half_spread_bps, impact_bps_per_lakh)
        total_assumed_pnl += result["pnl"]
        eod = result.get("eod_price", ohlc["close"])

        outcomes.append({
            "symbol": symbol,
            "pnl": result["pnl"],
            "scenario": result["scenario"],
            "eod_price": eod,
            "fill_price": result.get("fill_price"),
            "day_open": ohlc["open"],
            "day_high": ohlc["high"],
            "day_low": ohlc["low"],
            "entry": pos.get("assumed_entry") or pos.get("entry_price"),
            "stop": pos.get("stop"),
            "target1": pos.get("target1"),
            "target2": pos.get("target2"),
            "qty": pos.get("qty"),
            "pattern": pos.get("pattern", "N/A"),
            "score": pos.get("score"),
            "target2_rr": pos.get("target2_rr"),
            "atr_used": pos.get("atr_used"),
            "rr": round((pos.get("target1", 0) - (pos.get("assumed_entry") or pos.get("entry_price", 0))) /
                        max(0.01, (pos.get("assumed_entry") or pos.get("entry_price", 1)) - pos.get("stop", 0)), 2),
        })

        logger.info(
            "dry_run_outcome",
            symbol=symbol,
            entry=pos.get("assumed_entry") or pos.get("entry_price"),
            eod=eod,
            scenario=result["scenario"],
            pnl=result["pnl"],
        )

    # Append to the trade journal — the dataset for evaluating the adaptive
    # target logic (and future RL tuning of its breakpoints).
    journal_total = 0
    try:
        from autotrader.core.trade_journal import append_outcomes, count_rows
        append_outcomes(
            run_date=state.get("run_date", ""),
            regime=state.get("market_regime", "unknown"),
            dry_run=dry_run,
            outcomes=outcomes,
        )
        journal_total = count_rows()
        # Passive heartbeat: cumulative dataset size for the adaptive-RR review.
        logger.info("trade_journal_heartbeat", total_trades=journal_total,
                    added_today=len(outcomes))
    except Exception as exc:
        logger.warning("trade_journal_call_failed", error=str(exc))

    entry = audit_entry(agent=AGENT_NAME, action="dry_run_pnl_computed", data={
        "positions": len(positions),
        "total_assumed_pnl": round(total_assumed_pnl, 2),
        "outcomes": outcomes,
    })

    return {
        "trade_outcomes": outcomes,
        "daily_pnl": total_assumed_pnl,
        "journal_total": journal_total,
        "audit_trail": [entry],
    }

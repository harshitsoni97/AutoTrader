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
    """Fetch today's closing price for a symbol.

    Strategy:
    1. LTP (last traded price) — after 15:30 IST this equals the closing price.
       Works even when historical daily candles haven't been finalized yet.
    2. Historical daily candle — fallback; returns yesterday's close if today's
       candle isn't yet settled, so only use when LTP is unavailable.
    """
    try:
        from autotrader.agents.layer2.technical_structure import _load_instrument_map
        from autotrader.tools import upstox_data
        imap = _load_instrument_map()
        ikey = imap.get(symbol)
        if not ikey:
            return None

        # Primary: LTP after market close = today's closing price
        ltp_data = upstox_data.get_ltp([ikey])
        if ltp_data and ikey in ltp_data:
            price = float(ltp_data[ikey])
            if price > 0:
                logger.info("eod_price_via_ltp", symbol=symbol, price=price)
                return price

        # Fallback: most recent settled daily candle
        today = date.today().isoformat()
        from_date = (date.today() - timedelta(days=5)).isoformat()
        rows = upstox_data.get_historical_candles(ikey, "days", 1, from_date, today)
        if rows:
            rows.sort(key=lambda r: r.get("timestamp", ""))
            price = float(rows[-1]["close"])
            logger.info("eod_price_via_candle", symbol=symbol, price=price,
                        candle_date=rows[-1].get("timestamp", "")[:10])
            return price
    except Exception as exc:
        logger.warning("eod_price_fetch_failed", symbol=symbol, error=str(exc))
    return None


def _simulate_pnl(pos: dict, eod: float) -> dict:
    """Simulate outcome for one position given EOD price."""
    entry = pos.get("assumed_entry") or pos.get("entry_price", 0)
    stop = pos.get("stop", 0)
    target1 = pos.get("target1", 0)
    target2 = pos.get("target2", 0)
    qty = pos.get("qty", 0)

    if not entry or not qty:
        return {"pnl": 0.0, "scenario": "no_data"}

    if eod >= target2:
        pnl = qty * (target2 - entry)
        scenario = "target2_hit"
    elif eod >= target1:
        half = max(1, qty // 2)
        pnl = half * (target1 - entry) + (qty - half) * (eod - entry)
        scenario = "target1_hit_partial"
    elif eod <= stop:
        pnl = qty * (stop - entry)
        scenario = "stopped_out"
    else:
        pnl = qty * (eod - entry)
        scenario = "open_at_close"

    return {"pnl": round(pnl, 2), "scenario": scenario, "eod_price": eod}


def dry_run_pnl_agent(state: TradingState) -> dict[str, Any]:
    logger.info("dry_run_pnl_starting")

    positions = state.get("positions", [])
    dry_run = state.get("dry_run", True)

    if not dry_run:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="skipped_live_mode", data={})]}

    if not positions:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_positions", data={})]}

    outcomes = []
    total_assumed_pnl = 0.0

    for pos in positions:
        symbol = pos.get("symbol", "")
        eod = _eod_price(symbol)
        if eod is None:
            logger.warning("no_eod_price", symbol=symbol)
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

        result = _simulate_pnl(pos, eod)
        total_assumed_pnl += result["pnl"]

        outcomes.append({
            "symbol": symbol,
            "pnl": result["pnl"],
            "scenario": result["scenario"],
            "eod_price": eod,
            "entry": pos.get("assumed_entry") or pos.get("entry_price"),
            "stop": pos.get("stop"),
            "target1": pos.get("target1"),
            "target2": pos.get("target2"),
            "qty": pos.get("qty"),
            "pattern": pos.get("pattern", "N/A"),
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

    entry = audit_entry(agent=AGENT_NAME, action="dry_run_pnl_computed", data={
        "positions": len(positions),
        "total_assumed_pnl": round(total_assumed_pnl, 2),
        "outcomes": outcomes,
    })

    return {
        "trade_outcomes": outcomes,
        "daily_pnl": total_assumed_pnl,
        "audit_trail": [entry],
    }

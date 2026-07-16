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


def _day_ohlc(symbol: str, target_date: str | None = None) -> dict | None:
    """Trade-day OHLC for a symbol by aggregating that day's 30-min candles.

    target_date (YYYY-MM-DD) is the TRADING day to price — pass the session's
    run_date, NOT date.today(). Post-market often runs after local midnight (log
    timestamps are UTC), so date.today() can roll to the next day and find no
    candles. Returns {open, high, low, close, date} or None.
    """
    try:
        from autotrader.tools.price_utils import _instrument_key
        from autotrader.tools import upstox_data
        ikey = _instrument_key(symbol)
        if not ikey:
            return None
        day = target_date or date.today().isoformat()
        d = date.fromisoformat(day)
        # Narrow window: Upstox's 30-min intraday endpoint drops the most-recent
        # day when from_date reaches too far back. d-2 covers a weekend gap while
        # still returning the run_date's candles.
        frm = (d - timedelta(days=2)).isoformat()
        to = (d + timedelta(days=1)).isoformat()
        rows = upstox_data.get_historical_candles(ikey, "minutes", 30, frm, to)
        todays = [r for r in (rows or []) if str(r.get("timestamp", "")).startswith(day)]
        if not todays:
            logger.warning("no_intraday_candles_today", symbol=symbol, target_date=day)
            return None
        todays.sort(key=lambda r: r.get("timestamp", ""))
        return {
            "open": float(todays[0]["open"]),
            "high": max(float(r["high"]) for r in todays),
            "low": min(float(r["low"]) for r in todays),
            "close": float(todays[-1]["close"]),
            "date": day,
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
    run_date = state.get("run_date") or None

    # Realized P&L is ALREADY known at close: the intraday MonitoringAgent booked
    # every stop/T1/T2 exit at live prices during the session and accumulated it into
    # state["daily_pnl"] (it reduces qty on a partial, so the running sum is exact).
    # Trust that — no candle re-simulation — and only mark STILL-OPEN positions to the
    # close. This makes the summary COMPLETE at 15:35 IST instead of deferring to next
    # morning waiting on 30-min candles that Upstox publishes late in the evening.
    from autotrader.tools.price_utils import live_ltp

    realized_total = round(state.get("daily_pnl", 0.0) or 0.0, 2)
    open_mtm = 0.0
    for pos in positions:
        symbol = pos.get("symbol", "")
        status = (pos.get("status") or "OPEN").upper()
        entry = pos.get("entry_price") or pos.get("assumed_entry") or 0
        qty = pos.get("qty", 0) or 0

        if status in ("STOPPED", "TARGET2_HIT", "CLOSED"):
            # Closed intraday — its realized P&L is already inside realized_total.
            outcomes.append({
                "symbol": symbol, "pnl": pos.get("realized_pnl", 0.0),
                "scenario": "realized_" + status.lower(), "eod_price": pos.get("exit_price"),
                "fill_price": entry, "entry": entry, "stop": pos.get("stop"),
                "target1": pos.get("target1"), "target2": pos.get("target2"), "qty": qty,
                "pattern": pos.get("pattern", "N/A"), "score": pos.get("score"),
            })
            continue

        # Still open at EOD — mark remaining qty to the close. Live LTP returns the
        # closing price right at 15:35 IST; fall back to the day's candle close.
        close_px = live_ltp(symbol)
        if not close_px or close_px <= 0:
            ohlc = _day_ohlc(symbol, run_date)
            close_px = ohlc["close"] if ohlc else None
        if not close_px or close_px <= 0:
            outcomes.append({
                "symbol": symbol, "pnl": 0.0, "scenario": "price_unavailable",
                "entry": entry, "stop": pos.get("stop"), "target1": pos.get("target1"),
                "target2": pos.get("target2"), "qty": qty,
            })
            continue
        mtm = (close_px - entry) * qty
        open_mtm += mtm
        outcomes.append({
            "symbol": symbol, "pnl": round(mtm, 2), "scenario": "marked_to_close",
            "eod_price": round(close_px, 2), "fill_price": entry, "entry": entry,
            "stop": pos.get("stop"), "target1": pos.get("target1"), "target2": pos.get("target2"),
            "qty": qty, "pattern": pos.get("pattern", "N/A"), "score": pos.get("score"),
        })
        logger.info("dry_run_mark_to_close", symbol=symbol, entry=entry, close=close_px, mtm=round(mtm, 2))

    total_assumed_pnl = round(realized_total + open_mtm, 2)

    # If any position couldn't be priced (candles not published yet), the day is
    # incomplete — skip journaling and flag it so post-market can defer to the
    # next run instead of recording a false ₹0.
    pricing_incomplete = any(o.get("scenario") == "price_unavailable" for o in outcomes)

    journal_total = 0
    if pricing_incomplete:
        logger.warning("dry_run_pricing_incomplete", run_date=state.get("run_date"),
                       unpriced=[o["symbol"] for o in outcomes if o.get("scenario") == "price_unavailable"])
    else:
        # Append to the trade journal — the dataset for evaluating the adaptive
        # target logic (and future RL tuning of its breakpoints).
        try:
            from autotrader.core.trade_journal import append_outcomes, count_rows
            append_outcomes(
                run_date=state.get("run_date", ""),
                regime=state.get("market_regime", "unknown"),
                dry_run=dry_run,
                outcomes=outcomes,
            )
            journal_total = count_rows()
            logger.info("trade_journal_heartbeat", total_trades=journal_total,
                        added_today=len(outcomes))
        except Exception as exc:
            logger.warning("trade_journal_call_failed", error=str(exc))

    entry = audit_entry(agent=AGENT_NAME, action="dry_run_pnl_computed", data={
        "positions": len(positions),
        "total_assumed_pnl": round(total_assumed_pnl, 2),
        "pricing_incomplete": pricing_incomplete,
        "outcomes": outcomes,
    })

    return {
        "trade_outcomes": outcomes,
        "daily_pnl": total_assumed_pnl,
        "pricing_incomplete": pricing_incomplete,
        "journal_total": journal_total,
        "audit_trail": [entry],
    }

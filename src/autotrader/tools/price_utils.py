"""Shared price helpers — single Upstox source of truth for all agents.

Both dry-run P&L and the compete leaderboard must price positions identically,
otherwise the two numbers disagree. This module is that single source: it maps a
symbol to its Upstox instrument key and fetches prices via the Upstox Analytics
API (LTP + historical candles). No yfinance.
"""

from __future__ import annotations

import structlog
from datetime import date, timedelta

logger = structlog.get_logger()


def _instrument_key(symbol: str) -> str | None:
    """Resolve an NSE symbol to its Upstox instrument key via the instrument map."""
    try:
        from autotrader.agents.layer2.technical_structure import _load_instrument_map
        return _load_instrument_map().get(symbol)
    except Exception as exc:
        logger.warning("instrument_key_lookup_failed", symbol=symbol, error=str(exc))
        return None


def live_ltp(symbol: str) -> float | None:
    """Current last-traded price for a symbol via Upstox LTP. None on failure."""
    ikey = _instrument_key(symbol)
    if not ikey:
        return None
    try:
        from autotrader.tools import upstox_data
        data = upstox_data.get_ltp([ikey])
        if data and ikey in data:
            price = float(data[ikey])
            if price > 0:
                return price
    except Exception as exc:
        logger.warning("live_ltp_failed", symbol=symbol, error=str(exc))
    return None


def closing_price(symbol: str, require_today: bool = True, target_date: str | None = None) -> float | None:
    """Closing price for a symbol via Upstox for the given trading day.

    target_date (YYYY-MM-DD) is the trading day to price — pass the session's
    run_date. Post-market often runs after local midnight (date.today() rolls to
    the next day and finds no candles), so callers should pass run_date explicitly.

    Strategy:
      1. TODAY's last 30-min intraday candle close (reliable same-day source).
      2. LTP — only when pricing the actual current day (no target_date), since
         after the trading day has passed LTP no longer reflects that day's close.
      3. Daily candle, validated against the target day.
    """
    ikey = _instrument_key(symbol)
    if not ikey:
        return None

    from autotrader.tools import upstox_data
    day = target_date or date.today().isoformat()
    d = date.fromisoformat(day)

    # Fallback 1 (now primary): the target day's last 30-min intraday candle.
    # Narrow window — Upstox's 30-min endpoint drops the most-recent day when
    # from_date reaches too far back.
    try:
        frm = (d - timedelta(days=2)).isoformat()
        to = (d + timedelta(days=1)).isoformat()
        mins = upstox_data.get_historical_candles(ikey, "minutes", 30, frm, to)
        todays = [r for r in (mins or []) if str(r.get("timestamp", "")).startswith(day)]
        if todays:
            todays.sort(key=lambda r: r.get("timestamp", ""))
            return float(todays[-1]["close"])
    except Exception as exc:
        logger.warning("closing_price_intraday_failed", symbol=symbol, error=str(exc))

    # Fallback 2: LTP — only meaningful for the live current day.
    if target_date is None:
        ltp = live_ltp(symbol)
        if ltp is not None:
            logger.info("closing_price_via_ltp", symbol=symbol, price=ltp)
            return ltp

    today = day  # for the daily-candle validation below

    # Fallback 3: most recent settled daily candle (validated against the day).
    try:
        from_date = (d - timedelta(days=5)).isoformat()
        rows = upstox_data.get_historical_candles(ikey, "days", 1, from_date, today)
        if rows:
            rows.sort(key=lambda r: r.get("timestamp", ""))
            last = rows[-1]
            candle_date = str(last.get("timestamp", ""))[:10]
            if require_today and candle_date != today:
                logger.info("closing_price_stale_candle", symbol=symbol,
                            candle_date=candle_date, today=today)
                return None
            return float(last["close"])
    except Exception as exc:
        logger.warning("closing_price_candle_failed", symbol=symbol, error=str(exc))
    return None

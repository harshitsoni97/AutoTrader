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


def closing_price(symbol: str, require_today: bool = True) -> float | None:
    """Today's closing price for a symbol via Upstox.

    Strategy:
      1. LTP — after 15:30 IST this equals the closing price, and works even
         before the daily candle is finalized.
      2. Historical daily candle fallback. When require_today is True (default)
         we only accept a candle dated today; on a holiday/weekend the latest
         candle is the prior trading day, so we return None (no phantom P&L).
    """
    ikey = _instrument_key(symbol)
    if not ikey:
        return None

    # Primary: LTP after close == closing price
    ltp = live_ltp(symbol)
    if ltp is not None:
        logger.info("closing_price_via_ltp", symbol=symbol, price=ltp)
        return ltp

    # Fallback: most recent settled daily candle (validated against today)
    try:
        from autotrader.tools import upstox_data
        today = date.today().isoformat()
        from_date = (date.today() - timedelta(days=5)).isoformat()
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

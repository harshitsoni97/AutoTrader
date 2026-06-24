"""Relative Strength Agent — ranks stocks vs Nifty and sector peers."""

from __future__ import annotations

import structlog
from typing import Any

import json
import os
from datetime import date, timedelta

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import get_nifty_data

logger = structlog.get_logger()

AGENT_NAME = "RelativeStrengthAgent"

SECTOR_WATCHLIST = {
    "Banking": ["HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN"],
    "Capital_Goods": ["LT", "BEL", "HAL", "BHEL"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH"],
    "Pharma": ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB"],
    "Auto": ["MARUTI", "TMCV", "TMPV", "MM", "BAJAJ-AUTO"],
    "Realty": ["DLF", "GODREJPROP", "OBEROIRLTY"],
    "Metal": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "Energy": ["RELIANCE", "ONGC", "BPCL"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND"],
    "Midcap": ["POLYCAB", "DELHIVERY", "ETERNAL", "IRCTC"],
}


def _pct_change(rows: list[dict], lookback: int = 5) -> float:
    if len(rows) < 2:
        return 0.0
    n = min(lookback, len(rows) - 1)
    return (rows[-1]["close"] / rows[-n]["close"] - 1) * 100


def _rs_score(stock_ret: float, nifty_ret: float) -> float:
    """Normalize relative strength to 0–100 scale."""
    if nifty_ret == 0:
        return 50.0
    ratio = stock_ret / abs(nifty_ret) if nifty_ret != 0 else 1.0
    # Map ratio: -3 -> 0, 0 -> 50, +3 -> 100
    score = 50 + (ratio * 16.67)
    return max(0.0, min(100.0, score))


_RS_INSTRUMENT_MAP: dict | None = None


def _get_rs_rows(symbol: str) -> list[dict]:
    """Fetch daily rows via Upstox (primary) for RS computation."""
    global _RS_INSTRUMENT_MAP
    if _RS_INSTRUMENT_MAP is None:
        map_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../../../config/upstox_instruments.json"))
        try:
            with open(map_path) as f:
                _RS_INSTRUMENT_MAP = json.load(f)
        except Exception:
            _RS_INSTRUMENT_MAP = {}

    ikey = _RS_INSTRUMENT_MAP.get(symbol)
    if ikey:
        try:
            from autotrader.tools import upstox_data
            today_str = date.today().strftime("%Y-%m-%d")
            from_str = (date.today() - timedelta(days=15)).strftime("%Y-%m-%d")
            rows = upstox_data.get_historical_candles(ikey, "days", 1, from_str, today_str)
            if rows and len(rows) >= 2:
                rows.sort(key=lambda r: r.get("timestamp", ""))
                return rows
        except Exception as exc:
            logger.debug("[%s] Upstox RS fetch failed for %s: %s", AGENT_NAME, symbol, exc)

    # Fallback to market_data (which has its own Upstox fallback)
    from autotrader.tools.market_data import get_stock_data
    return get_stock_data(symbol, period="10d") or []


def relative_strength_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Calculating relative strength", AGENT_NAME)

    nifty = get_nifty_data()
    nifty_ret_5d = _pct_change(nifty, 5)
    nifty_ret_1d = _pct_change(nifty, 1)

    # Build candidate list from catalysts + top sector watchlists
    catalyst_symbols = {c["symbol"] for c in state.get("catalysts", [])}
    sector_symbols: set[str] = set()
    for sector in state.get("top_sectors", []):
        sector_symbols.update(SECTOR_WATCHLIST.get(sector, []))

    all_symbols = list(catalyst_symbols | sector_symbols)[:25]

    candidates: list[dict] = []
    for symbol in all_symbols:
        rows = _get_rs_rows(symbol)
        if not rows or len(rows) < 2:
            continue
        ret_5d = _pct_change(rows, 5)
        ret_1d = _pct_change(rows, 1)
        rs_vs_nifty = _rs_score(ret_5d, nifty_ret_5d)

        # Find catalyst info for this symbol
        cat_data = next((c for c in state.get("catalysts", []) if c["symbol"] == symbol), {})

        candidates.append({
            "symbol": symbol,
            "ret_1d_pct": round(ret_1d, 3),
            "ret_5d_pct": round(ret_5d, 3),
            "relative_strength": round(rs_vs_nifty, 1),
            "current_price": rows[-1]["close"],
            "catalyst_score": cat_data.get("catalyst_score", 0),
            "catalyst_reason": cat_data.get("reason", ""),
        })

    candidates.sort(key=lambda x: x["relative_strength"], reverse=True)

    msg = create_message(
        source=AGENT_NAME,
        target="VolumeIntelligenceAgent",
        payload={"candidates_count": len(candidates)},
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="rs_calculated",
        data={"candidates": len(candidates), "nifty_5d_ret": round(nifty_ret_5d, 3)},
    )

    logger.info("[%s] %d candidates ranked by relative strength", AGENT_NAME, len(candidates))

    return {
        "candidates": candidates,
        "messages": [msg],
        "audit_trail": [entry],
    }

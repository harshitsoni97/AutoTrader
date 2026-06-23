"""Technical Structure Agent — detects patterns, EMA alignment, RSI, ADX, VWAP."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import get_stock_data

logger = logging.getLogger(__name__)

AGENT_NAME = "TechnicalStructureAgent"

_INSTRUMENT_MAP: dict[str, str] | None = None
_STRATEGY_PARAMS: dict | None = None


def _load_strategy_params() -> dict:
    global _STRATEGY_PARAMS
    params_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "../../../../config/strategy_params.json")
    )
    try:
        with open(params_path) as f:
            _STRATEGY_PARAMS = json.load(f)
    except Exception:
        _STRATEGY_PARAMS = {}
    return _STRATEGY_PARAMS


def _load_instrument_map() -> dict[str, str]:
    global _INSTRUMENT_MAP
    if _INSTRUMENT_MAP is not None:
        return _INSTRUMENT_MAP
    map_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "../../../../config/upstox_instruments.json")
    )
    try:
        with open(map_path) as f:
            _INSTRUMENT_MAP = json.load(f)
    except Exception:
        _INSTRUMENT_MAP = {}
    return _INSTRUMENT_MAP


def _ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return [prices[-1]] * len(prices) if prices else []
    k = 2 / (period + 1)
    emas = [sum(prices[:period]) / period]
    for p in prices[period:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return [emas[0]] * (period - 1) + emas


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas[-period:]]
    losses = [abs(min(0, d)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _atr(rows: list[dict], period: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    trs = []
    for i in range(1, len(rows)):
        tr = max(
            rows[i]["high"] - rows[i]["low"],
            abs(rows[i]["high"] - rows[i - 1]["close"]),
            abs(rows[i]["low"] - rows[i - 1]["close"]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs))


def _adx(rows: list[dict], period: int = 14) -> float:
    """Simplified ADX calculation."""
    if len(rows) < period + 2:
        return 20.0
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(rows)):
        up_move = rows[i]["high"] - rows[i - 1]["high"]
        down_move = rows[i - 1]["low"] - rows[i]["low"]
        plus_dms.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dms.append(down_move if down_move > up_move and down_move > 0 else 0)
        trs.append(max(
            rows[i]["high"] - rows[i]["low"],
            abs(rows[i]["high"] - rows[i - 1]["close"]),
            abs(rows[i]["low"] - rows[i - 1]["close"]),
        ))
    smooth = lambda arr: sum(arr[-period:]) / period
    atr_s = smooth(trs) or 1
    di_plus = 100 * smooth(plus_dms) / atr_s
    di_minus = 100 * smooth(minus_dms) / atr_s
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
    return round(dx, 2)


def _vwap(rows: list[dict]) -> float:
    """Daily VWAP from last session's typical price (open + high + low + close / 4)."""
    if not rows:
        return 0.0
    day = rows[-1]
    typical = (day["open"] + day["high"] + day["low"] + day["close"]) / 4
    return round(typical, 2)


def _bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[float, float, float]:
    """Returns (upper, middle, lower) Bollinger Bands."""
    if len(closes) < period:
        mid = closes[-1] if closes else 0.0
        return mid, mid, mid
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    return round(mid + std_dev * std, 2), round(mid, 2), round(mid - std_dev * std, 2)


def _bb_squeeze(closes: list[float], period: int = 20, lookback: int = 50) -> bool:
    """True when current BB width is compressed below 75% of its recent average — pre-breakout accumulation."""
    if len(closes) < period + lookback:
        return False

    def norm_width(window: list[float]) -> float:
        mid = sum(window) / len(window)
        if mid == 0:
            return 0.0
        variance = sum((x - mid) ** 2 for x in window) / len(window)
        return (2 * 2.0 * variance ** 0.5) / mid

    n = len(closes)
    current_w = norm_width(closes[-period:])
    past_widths = [
        norm_width(closes[n - lookback - period + i: n - period + i])
        for i in range(lookback)
    ]
    avg_w = sum(past_widths) / len(past_widths) if past_widths else 0.0
    return avg_w > 0 and current_w < avg_w * 0.75


def _get_intraday_rows(symbol: str) -> list[dict]:
    """Fetch 30-minute candles from Upstox for the past 7 calendar days."""
    try:
        from autotrader.tools import upstox_data
        imap = _load_instrument_map()
        ikey = imap.get(symbol)
        if not ikey:
            return []
        today_str = date.today().strftime("%Y-%m-%d")
        from_str = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
        rows = upstox_data.get_historical_candles(ikey, "minutes", 30, from_str, today_str)
        if rows:
            rows.sort(key=lambda r: r.get("timestamp", ""))
        return rows or []
    except Exception as exc:
        logger.debug("[%s] Intraday candles failed for %s: %s", AGENT_NAME, symbol, exc)
        return []


def _intraday_atr(intraday_rows: list[dict], period: int = 14) -> float | None:
    """Compute ATR from 30-min candles. Returns None if insufficient data."""
    if len(intraday_rows) < max(period, 10):
        return None
    return round(_atr(intraday_rows, period), 2)


def _get_orb_data(intraday_rows: list[dict]) -> dict | None:
    """
    Extract today's Opening Range from the first 30-min candle (9:15-9:45 AM IST).
    Returns {"high": ..., "low": ...} or None if today's data is unavailable.
    """
    if not intraday_rows:
        return None
    today_str = date.today().strftime("%Y-%m-%d")
    today_candles = [r for r in intraday_rows if r.get("timestamp", "").startswith(today_str)]
    if not today_candles:
        return None
    today_candles.sort(key=lambda r: r.get("timestamp", ""))
    first = today_candles[0]
    return {"high": first["high"], "low": first["low"]}


def _detect_pattern(
    rows: list[dict],
    ema9: float,
    ema21: float,
    ema50: float,
    rsi: float,
    vwap: float,
    closes: list[float],
    orb_data: dict | None,
) -> str:
    if len(rows) < 5:
        return "NONE"

    today = rows[-1]
    current_price = today["close"]
    ema_aligned = ema9 > ema21 > ema50

    # BB Squeeze — detected before VWAP gate (pre-breakout signal regardless of direction)
    if _bb_squeeze(closes) and ema_aligned:
        return "BB_SQUEEZE"

    # VWAP gate: skip all bullish entry patterns when price is below VWAP
    above_vwap = current_price > vwap if vwap > 0 else True
    if not above_vwap:
        return "NONE"

    # ORB — entry on candle close cleanly above first 30-min candle high
    if orb_data and ema_aligned and current_price > orb_data["high"] * 1.001:
        return "ORB"

    # Classic breakout above prior 4-day high
    prev_high = max(r["high"] for r in rows[-5:-1])
    if ema_aligned and today["close"] > prev_high * 1.005:
        return "BREAKOUT"

    # VWAP cross: price crossed VWAP from below during the session
    if today["low"] < vwap < today["close"] and ema_aligned:
        return "VWAP_CROSS"

    # Plain EMA alignment with momentum
    if ema_aligned and rsi > 50:  # intentionally fixed at 50 — weakest signal
        return "EMA_ALIGNMENT"

    return "NONE"


def _technical_score(
    ema9: float,
    ema21: float,
    ema50: float,
    rsi: float,
    adx: float,
    pattern: str,
    above_vwap: bool,
) -> float:
    # ADX gate: ranging/choppy market — zero score prevents false breakout entries
    sp = _load_strategy_params()
    adx_threshold = sp.get("adx_threshold", 20)
    rsi_min_param = sp.get("rsi_min", 50)
    if adx < adx_threshold:
        return 0.0

    score = 0.0

    # Trend via EMA (30 pts)
    if ema9 > ema21 > ema50:
        score += 30
    elif ema9 > ema21:
        score += 15

    # RSI momentum health (20 pts)
    rsi_mid = rsi_min_param + 5
    if rsi_mid <= rsi <= 75:
        score += 20
    elif rsi_min_param <= rsi < rsi_mid:
        score += 12
    elif rsi > 75:
        score += 8  # overbought — momentum present but reversal risk

    # ADX trend strength (20 pts)
    if adx > 30:
        score += 20
    elif adx > 25:
        score += 15
    elif adx > 20:
        score += 10

    # VWAP institutional bias (10 pts)
    if above_vwap:
        score += 10

    # Pattern quality (20 pts)
    pattern_pts = {
        "ORB": 20,
        "BREAKOUT": 20,
        "BB_SQUEEZE": 18,
        "VWAP_CROSS": 16,
        "EMA_ALIGNMENT": 10,
        "NONE": 0,
    }
    score += pattern_pts.get(pattern, 0)

    return min(100.0, round(score, 1))


def technical_structure_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running technical analysis", AGENT_NAME)

    candidates = state.get("candidates", [])
    updated: list[dict] = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        daily_rows = get_stock_data(symbol, period="60d")

        # Fetch intraday rows early — used as fallback if daily unavailable
        intraday_rows = _get_intraday_rows(symbol)
        intra_atr = _intraday_atr(intraday_rows)
        orb_data = _get_orb_data(intraday_rows)

        # Prefer daily rows for EMA/RSI/ADX; fall back to intraday 30-min candles
        # when daily data is blocked (e.g. yfinance 403 on cloud servers).
        # 65 × 30-min candles (5 trading days) cover EMA50, ADX14, RSI14 comfortably.
        rows = daily_rows if (daily_rows and len(daily_rows) >= 20) else intraday_rows
        data_source = "daily" if (daily_rows and len(daily_rows) >= 20) else "intraday_30m"

        if not rows or len(rows) < 20:
            candidate["technical_score"] = 0.0
            candidate["pattern"] = "NONE"
            updated.append(candidate)
            continue

        closes = [r["close"] for r in rows]
        ema9_series = _ema(closes, 9)
        ema21_series = _ema(closes, 21)
        ema50_series = _ema(closes, 50)

        ema9 = ema9_series[-1]
        ema21 = ema21_series[-1]
        ema50 = ema50_series[-1] if len(ema50_series) >= 50 else ema21

        rsi = _rsi(closes, 14)
        adx = _adx(rows, 14)
        vwap = _vwap(rows)
        current_price = rows[-1]["close"]
        above_vwap = current_price > vwap if vwap > 0 else True

        # ATR: prefer intraday (realistic stops), fall back to daily
        daily_atr = _atr(daily_rows, 14) if daily_rows else 0.0
        atr = intra_atr if (intra_atr and intra_atr > 0) else daily_atr

        bb_upper, bb_mid, bb_lower = _bollinger_bands(closes)
        pattern = _detect_pattern(rows, ema9, ema21, ema50, rsi, vwap, closes, orb_data)
        tscore = _technical_score(ema9, ema21, ema50, rsi, adx, pattern, above_vwap)

        candidate = {
            **candidate,
            "technical_score": tscore,
            "pattern": pattern,
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "ema50": round(ema50, 2),
            "rsi": rsi,
            "adx": adx,
            "vwap": vwap,
            "current_price": round(current_price, 2),
            "atr": round(atr, 2),
            "atr_source": "intraday_30m" if intra_atr else data_source,
            "indicators_source": data_source,
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            "above_vwap": above_vwap,
            "orb_high": orb_data["high"] if orb_data else None,
            "orb_low": orb_data["low"] if orb_data else None,
        }
        updated.append(candidate)

        adx_gate = "PASS" if adx >= 20 else f"BLOCKED(ADX={adx})"
        logger.debug(
            "[%s] %s adx_gate=%s pattern=%s score=%.1f atr=%.2f(%s) above_vwap=%s",
            AGENT_NAME, symbol, adx_gate, pattern, tscore, atr,
            candidate["atr_source"], above_vwap,
        )

    msg = create_message(
        source=AGENT_NAME,
        target="OpportunityScoringAgent",
        payload={"analyzed": len(updated)},
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="technical_analysis_complete",
        data={"candidates": len(updated), "patterns": {c["symbol"]: c.get("pattern") for c in updated[:5]}},
    )

    logger.info("[%s] Technical analysis done for %d symbols", AGENT_NAME, len(updated))

    return {
        "candidates": updated,
        "messages": [msg],
        "audit_trail": [entry],
    }

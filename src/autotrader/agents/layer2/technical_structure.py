"""Technical Structure Agent — detects patterns, EMA alignment, RSI, ADX, VWAP."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import get_stock_data

logger = logging.getLogger(__name__)

AGENT_NAME = "TechnicalStructureAgent"


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
    """Daily VWAP from available OHLCV rows (approximation using last session)."""
    if not rows:
        return 0.0
    day = rows[-1]
    typical = (day["high"] + day["low"] + day["close"]) / 3
    return round(typical, 2)


def _detect_pattern(rows: list[dict], ema9: float, ema21: float, ema50: float, rsi: float) -> str:
    if len(rows) < 5:
        return "NONE"
    today = rows[-1]
    prev_high = max(r["high"] for r in rows[-5:-1])
    breakout = today["close"] > prev_high * 1.005
    ema_aligned = ema9 > ema21 > ema50
    vwap = _vwap(rows)
    vwap_cross = today["low"] < vwap < today["close"]
    # Opening Range Breakout: close above first 15-min high (approx: close > day open + 0.3%)
    orb = today["close"] > today["open"] * 1.003

    if ema_aligned and breakout:
        return "BREAKOUT"
    if ema_aligned and orb and rsi > 55:
        return "ORB"
    if vwap_cross and ema_aligned:
        return "VWAP_CROSS"
    if ema_aligned and rsi > 50:
        return "EMA_ALIGNMENT"
    return "NONE"


def _technical_score(ema9: float, ema21: float, ema50: float, rsi: float, adx: float, pattern: str) -> float:
    score = 0.0
    # Trend (30 pts)
    if ema9 > ema21 > ema50:
        score += 30
    elif ema9 > ema21:
        score += 15
    # RSI (25 pts)
    if 55 <= rsi <= 75:
        score += 25
    elif 50 <= rsi < 55:
        score += 15
    elif rsi > 75:
        score += 10  # overbought risk
    # ADX (25 pts)
    if adx > 30:
        score += 25
    elif adx > 20:
        score += 15
    elif adx > 15:
        score += 8
    # Pattern (20 pts)
    pattern_pts = {"BREAKOUT": 20, "ORB": 18, "VWAP_CROSS": 16, "EMA_ALIGNMENT": 10, "NONE": 0}
    score += pattern_pts.get(pattern, 0)
    return min(100.0, round(score, 1))


def technical_structure_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running technical analysis", AGENT_NAME)

    candidates = state.get("candidates", [])
    updated: list[dict] = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        rows = get_stock_data(symbol, period="60d")

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
        atr = _atr(rows, 14)
        pattern = _detect_pattern(rows, ema9, ema21, ema50, rsi)
        tscore = _technical_score(ema9, ema21, ema50, rsi, adx, pattern)

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
            "atr": round(atr, 2),
        }
        updated.append(candidate)

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

"""Volume Intelligence Agent — detects volume shockers and institutional activity."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import get_stock_data

logger = logging.getLogger(__name__)

AGENT_NAME = "VolumeIntelligenceAgent"


def _avg_volume(rows: list[dict], days: int = 20) -> float:
    vols = [r["volume"] for r in rows[-days:] if r["volume"] > 0]
    return sum(vols) / len(vols) if vols else 0.0


def _volume_score(ratio: float) -> float:
    """Map volume ratio to 0–100 score. 3x volume = 99."""
    return min(100.0, ratio * 33.33)


def volume_intelligence_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Analyzing volume patterns", AGENT_NAME)

    candidates = state.get("candidates", [])
    updated: list[dict] = []

    for candidate in candidates:
        symbol = candidate["symbol"]
        rows = get_stock_data(symbol, period="25d")

        if not rows or len(rows) < 5:
            candidate["volume_score"] = 0.0
            candidate["volume_ratio"] = 0.0
            candidate["volume_alert"] = False
            updated.append(candidate)
            continue

        avg_vol = _avg_volume(rows, 20)
        today_vol = rows[-1]["volume"]

        ratio = today_vol / avg_vol if avg_vol > 0 else 1.0
        score = _volume_score(ratio)

        # Delivery spike heuristic: if high-low range is narrow relative to volume, likely delivery
        day = rows[-1]
        price_range = day["high"] - day["low"]
        delivery_spike = (price_range / day["close"]) < 0.015 and ratio > 2.0

        candidate = {
            **candidate,
            "volume_ratio": round(ratio, 2),
            "volume_score": round(score, 1),
            "avg_volume_20d": int(avg_vol),
            "today_volume": int(today_vol),
            "delivery_spike": delivery_spike,
            "volume_alert": ratio >= 2.0,
        }
        updated.append(candidate)

    # Sort by volume score (descending) within same RS tier
    updated.sort(key=lambda x: (round(x.get("relative_strength", 0) / 10), x.get("volume_score", 0)), reverse=True)

    msg = create_message(
        source=AGENT_NAME,
        target="TechnicalStructureAgent",
        payload={"volume_shockers": sum(1 for c in updated if c.get("volume_alert"))},
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="volume_analyzed",
        data={"candidates": len(updated), "volume_shockers": sum(1 for c in updated if c.get("volume_alert"))},
    )

    logger.info("[%s] Volume analysis complete. Shockers: %d", AGENT_NAME, sum(1 for c in updated if c.get("volume_alert")))

    return {
        "candidates": updated,
        "messages": [msg],
        "audit_trail": [entry],
    }

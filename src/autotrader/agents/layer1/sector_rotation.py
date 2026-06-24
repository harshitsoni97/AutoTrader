"""Sector Rotation Agent — ranks sectors by relative performance."""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.market_data import get_sector_etf_data

logger = structlog.get_logger()

AGENT_NAME = "SectorRotationAgent"


def _pct_change(rows: list[dict], lookback: int) -> float:
    if len(rows) < 2:
        return 0.0
    n = min(lookback, len(rows) - 1)
    return (rows[-1]["close"] / rows[-n]["close"] - 1) * 100


def _volume_breadth(rows: list[dict]) -> float:
    """Recent 5-day average volume vs prior 5-day average volume ratio."""
    if len(rows) < 10:
        return 1.0
    recent = sum(r["volume"] for r in rows[-5:]) / 5
    prior = sum(r["volume"] for r in rows[-10:-5]) / 5
    return recent / prior if prior > 0 else 1.0


def sector_rotation_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Analyzing sector rotation", AGENT_NAME)

    sector_data = get_sector_etf_data()
    rankings: list[dict] = []

    for sector, rows in sector_data.items():
        if not rows:
            continue
        ret_1d = _pct_change(rows, 1)
        ret_5d = _pct_change(rows, 5)
        breadth = _volume_breadth(rows)
        # Composite momentum score
        score = (ret_5d * 0.5 + ret_1d * 0.3 + (breadth - 1) * 10 * 0.2)
        rankings.append({
            "sector": sector,
            "ret_1d_pct": round(ret_1d, 3),
            "ret_5d_pct": round(ret_5d, 3),
            "volume_breadth": round(breadth, 3),
            "momentum_score": round(score, 3),
        })

    rankings.sort(key=lambda x: x["momentum_score"], reverse=True)
    top_sectors = [r["sector"] for r in rankings[:3]]

    msg = create_message(
        source=AGENT_NAME,
        target="CatalystIntelligenceAgent",
        payload={
            "top_sectors": top_sectors,
            "sector_rankings": rankings,
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="sectors_ranked",
        data={"top_sectors": top_sectors, "total_sectors": len(rankings)},
    )

    logger.info("[%s] Top sectors: %s", AGENT_NAME, top_sectors)

    return {
        "top_sectors": top_sectors,
        "sector_rankings": rankings,
        "messages": [msg],
        "audit_trail": [entry],
    }

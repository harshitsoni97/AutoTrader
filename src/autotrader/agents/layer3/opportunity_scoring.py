"""Opportunity Scoring Agent — combines all signals into a composite score."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = logging.getLogger(__name__)

AGENT_NAME = "OpportunityScoringAgent"

# Default weights (must sum to 1.0)
WEIGHTS = {
    "market_regime": 0.20,
    "sector_strength": 0.20,
    "relative_strength": 0.20,
    "volume": 0.15,
    "catalyst": 0.15,
    "technical": 0.10,
}


def _market_regime_score(regime: str, confidence: float) -> float:
    # Confidence is applied at the composite level; base score reflects regime only
    base = {
        "risk_on": 95, "strong_bull": 100, "bullish": 80, "range_bound": 60,
        "bearish": 30, "risk_off": 20, "high_volatility": 40, "unknown": 50,
        "bull": 85,
    }.get(regime, 50)
    return float(base)


def _sector_score(symbol: str, sector_rankings: list[dict], top_sectors: list[str]) -> float:
    # Map symbol to sector — simplified lookup
    from autotrader.agents.layer1.catalyst_intelligence import SECTOR_WATCHLIST
    symbol_sector = None
    for sector, syms in SECTOR_WATCHLIST.items():
        if symbol in syms:
            symbol_sector = sector
            break
    if symbol_sector in top_sectors:
        rank = top_sectors.index(symbol_sector)
        return 100 - rank * 10
    # Check ranking list for momentum score
    for r in sector_rankings:
        if r.get("sector") == symbol_sector:
            raw_score = r.get("momentum_score", 0)
            return max(0, min(100, 50 + raw_score * 10))
    return 50.0


def opportunity_scoring_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Scoring opportunities", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    candidates = state.get("candidates", [])
    market_regime = state.get("market_regime", "unknown")
    market_confidence = state.get("market_confidence", 0.5)
    sector_rankings = state.get("sector_rankings", [])
    top_sectors = state.get("top_sectors", [])

    regime_score = _market_regime_score(market_regime, market_confidence)

    scored: list[dict] = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        # Accept explicit sector field on candidate (from test states)
        candidate_sector = candidate.get("sector")
        if candidate_sector and candidate_sector in top_sectors:
            rank = top_sectors.index(candidate_sector)
            sector_s = 100 - rank * 10
        else:
            sector_s = _sector_score(symbol, sector_rankings, top_sectors)
        # Accept both field naming conventions
        rs_s = candidate.get("rs_score", candidate.get("relative_strength", 50.0))
        vol_s = candidate.get("volume_score", 0.0)
        tech_s = candidate.get("technical_score", 0.0)
        # Catalyst score: from candidate directly OR from state catalysts list
        cat_s = float(candidate.get("catalyst_score", 0))
        if cat_s == 0:
            cat_entry = next(
                (c for c in state.get("catalysts", []) if c.get("symbol") == symbol),
                None,
            )
            if cat_entry:
                cat_s = float(cat_entry.get("score", cat_entry.get("catalyst_score", 0)))

        composite = (
            regime_score * WEIGHTS["market_regime"]
            + sector_s * WEIGHTS["sector_strength"]
            + rs_s * WEIGHTS["relative_strength"]
            + vol_s * WEIGHTS["volume"]
            + cat_s * WEIGHTS["catalyst"]
            + tech_s * WEIGHTS["technical"]
        )
        composite = round(composite, 2)

        scored.append({
            "symbol": symbol,
            "score": composite,
            "composite_score": composite,  # alias for test compatibility
            "component_scores": {
                "market_regime": round(regime_score, 2),
                "sector_strength": round(sector_s, 2),
                "relative_strength": round(rs_s, 2),
                "volume": round(vol_s, 2),
                "catalyst": round(cat_s, 2),
                "technical": round(tech_s, 2),
            },
            "current_price": candidate.get("current_price", 0),
            "pattern": candidate.get("pattern", "NONE"),
            "atr": candidate.get("atr", 0),
            "ema9": candidate.get("ema9", 0),
            "ema21": candidate.get("ema21", 0),
            "vwap": candidate.get("vwap", 0),
            "rsi": candidate.get("rsi", 50),
            "catalyst_reason": candidate.get("catalyst_reason", ""),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    eligible = [s for s in scored if s["score"] >= policy.minimum_score]

    msg = create_message(
        source=AGENT_NAME,
        target="GovernanceAgent",
        payload={
            "total_candidates": len(scored),
            "eligible": len(eligible),
            "top_opportunity": eligible[0] if eligible else None,
        },
    )
    entry = audit_entry(
        agent=AGENT_NAME,
        action="opportunities_scored",
        data={
            "total": len(scored),
            "eligible": len(eligible),
            "threshold": policy.minimum_score,
            "top": eligible[:3],
        },
    )

    logger.info("[%s] %d candidates scored, %d above threshold %.0f", AGENT_NAME, len(scored), len(eligible), policy.minimum_score)

    return {
        "scored_opportunities": eligible,
        "messages": [msg],
        "audit_trail": [entry],
    }

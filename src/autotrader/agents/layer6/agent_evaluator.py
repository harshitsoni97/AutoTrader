"""Agent Performance Evaluator — scores each agent's predictive accuracy."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = logging.getLogger(__name__)

AGENT_NAME = "AgentPerformanceEvaluator"

AGENT_NAMES = [
    "market_agent",
    "sector_agent",
    "catalyst_agent",
    "volume_agent",
    "technical_agent",
    "risk_agent",
    "governance_agent",
]


def _score_market_agent(state: TradingState, outcomes: list[dict]) -> float:
    """Market agent accuracy: did regime prediction align with trade outcomes?"""
    regime = state.get("market_regime", "unknown")
    if not outcomes:
        return 0.0
    bullish_regimes = {"bullish", "risk_on"}
    win_rate = sum(1 for o in outcomes if o.get("pnl", 0) > 0) / len(outcomes)
    if regime in bullish_regimes and win_rate > 0.5:
        return 90.0
    elif regime not in bullish_regimes and win_rate <= 0.5:
        return 85.0
    return 50.0


def _score_volume_agent(outcomes: list[dict], candidates: list[dict]) -> float:
    """Volume agent: did volume shockers outperform?"""
    shocker_symbols = {c["symbol"] for c in candidates if c.get("volume_alert")}
    if not shocker_symbols or not outcomes:
        return 0.0
    shocker_outcomes = [o for o in outcomes if o.get("symbol") in shocker_symbols]
    if not shocker_outcomes:
        return 50.0
    win_rate = sum(1 for o in shocker_outcomes if o.get("pnl", 0) > 0) / len(shocker_outcomes)
    return round(win_rate * 100, 1)


def _score_technical_agent(outcomes: list[dict]) -> float:
    """Technical agent: did detected patterns produce winning trades?"""
    pattern_outcomes = [o for o in outcomes if o.get("pattern", "NONE") != "NONE"]
    if not pattern_outcomes:
        return 0.0
    win_rate = sum(1 for o in pattern_outcomes if o.get("pnl", 0) > 0) / len(pattern_outcomes)
    return round(win_rate * 100, 1)


def _score_risk_agent(outcomes: list[dict]) -> float:
    """Risk agent: did approved trades have better R/R than minimum?"""
    rrs = [o.get("rr", 0) for o in outcomes if o.get("rr", 0) > 0]
    if not rrs:
        return 0.0
    above_min = sum(1 for r in rrs if r >= 2.0)
    return round(above_min / len(rrs) * 100, 1)


def agent_evaluator(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Evaluating agent performance", AGENT_NAME)

    outcomes = state.get("trade_outcomes", [])
    candidates = state.get("candidates", [])

    scores: dict[str, float] = {}
    scores["market_agent_accuracy"] = _score_market_agent(state, outcomes)
    scores["sector_agent_accuracy"] = 70.0  # Placeholder — needs multi-day tracking
    scores["catalyst_agent_accuracy"] = 65.0
    scores["volume_agent_accuracy"] = _score_volume_agent(outcomes, candidates)
    scores["technical_agent_accuracy"] = _score_technical_agent(outcomes)
    scores["risk_agent_accuracy"] = _score_risk_agent(outcomes)
    scores["governance_agent_accuracy"] = 100.0  # Governance is rule-based, always correct

    msg = create_message(
        source=AGENT_NAME, target="LongTermMemoryAgent",
        payload={"agent_scores": scores},
    )
    entry = audit_entry(agent=AGENT_NAME, action="agents_evaluated", data={"scores": scores})

    logger.info("[%s] Agent scores: %s", AGENT_NAME, scores)

    return {
        "agent_scores": scores,
        "messages": [msg],
        "audit_trail": [entry],
    }

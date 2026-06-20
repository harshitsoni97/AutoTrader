"""Compete-mode graph.

Runs the full deterministic pre-market pipeline (market_regime → sector_rotation →
catalyst_intelligence → options_intelligence → relative_strength → volume_intelligence →
technical_structure → opportunity_scoring), then fans out to the compete_coordinator
which calls each configured competitor model's LLM scoring review.

In dry_run mode (default): stops after compete_coordinator — no real orders.
In actual mode with a primary set: the primary competitor's pick is promoted into
scored_opportunities, then the standard governance → risk → trade_construction →
execution chain runs normally.

The post-market compete_evaluator lives in the post-market graph and is called
at end of day to produce the final leaderboard.
"""

from langgraph.graph import END, StateGraph

from autotrader.agents.compete.coordinator import compete_coordinator_agent
from autotrader.agents.layer0.universe_builder import universe_builder_agent
from autotrader.agents.layer1.catalyst_intelligence import catalyst_intelligence_agent
from autotrader.agents.layer1.market_regime import market_regime_agent
from autotrader.agents.layer1.options_intelligence import options_intelligence_agent
from autotrader.agents.layer1.sector_rotation import sector_rotation_agent
from autotrader.agents.layer2.relative_strength import relative_strength_agent
from autotrader.agents.layer2.technical_structure import technical_structure_agent
from autotrader.agents.layer2.volume_intelligence import volume_intelligence_agent
from autotrader.agents.layer3.opportunity_scoring import opportunity_scoring_agent
from autotrader.agents.layer4.governance import governance_agent
from autotrader.agents.layer4.risk import risk_agent
from autotrader.agents.layer5.execution import execution_agent
from autotrader.agents.layer5.trade_construction import trade_construction_agent
from autotrader.core.config import load_config
from autotrader.core.state import TradingState


def _governance_router(state: TradingState) -> str:
    return "approved" if state.get("governance_approved") else "rejected"


def _risk_router(state: TradingState) -> str:
    return "passed" if state.get("risk_passed") else "failed"


def _compete_router(state: TradingState) -> str:
    """After compete_coordinator: proceed to governance only in actual mode with a primary."""
    cfg = load_config()
    if not cfg.compete.dry_run and cfg.compete.primary:
        return "execute"
    return "done"


def build_compete_graph():
    """Build and compile the compete-mode graph."""
    graph = StateGraph(TradingState)

    # Shared deterministic pipeline (identical to pre_market_graph)
    graph.add_node("universe_builder", universe_builder_agent)
    graph.add_node("market_regime", market_regime_agent)
    graph.add_node("sector_rotation", sector_rotation_agent)
    graph.add_node("catalyst_intelligence", catalyst_intelligence_agent)
    graph.add_node("options_intelligence", options_intelligence_agent)
    graph.add_node("relative_strength", relative_strength_agent)
    graph.add_node("volume_intelligence", volume_intelligence_agent)
    graph.add_node("technical_structure", technical_structure_agent)
    graph.add_node("opportunity_scoring", opportunity_scoring_agent)

    # Compete coordinator
    graph.add_node("compete_coordinator", compete_coordinator_agent)

    # Execution chain (only used in actual mode)
    graph.add_node("governance", governance_agent)
    graph.add_node("risk", risk_agent)
    graph.add_node("trade_construction", trade_construction_agent)
    graph.add_node("execution", execution_agent)

    # Deterministic pipeline edges (same fan-out/fan-in as pre_market)
    graph.set_entry_point("universe_builder")
    graph.add_edge("universe_builder", "market_regime")
    graph.add_edge("market_regime", "sector_rotation")
    graph.add_edge("market_regime", "catalyst_intelligence")
    graph.add_edge("market_regime", "options_intelligence")
    graph.add_edge("sector_rotation", "relative_strength")
    graph.add_edge("catalyst_intelligence", "relative_strength")
    graph.add_edge("options_intelligence", "relative_strength")
    graph.add_edge("relative_strength", "volume_intelligence")
    graph.add_edge("volume_intelligence", "technical_structure")
    graph.add_edge("technical_structure", "opportunity_scoring")
    graph.add_edge("opportunity_scoring", "compete_coordinator")

    # After compete: dry_run → END; actual+primary → governance chain
    graph.add_conditional_edges(
        "compete_coordinator",
        _compete_router,
        {"execute": "governance", "done": END},
    )
    graph.add_conditional_edges(
        "governance",
        _governance_router,
        {"approved": "risk", "rejected": END},
    )
    graph.add_conditional_edges(
        "risk",
        _risk_router,
        {"passed": "trade_construction", "failed": END},
    )
    graph.add_edge("trade_construction", "execution")
    graph.add_edge("execution", END)

    return graph.compile()

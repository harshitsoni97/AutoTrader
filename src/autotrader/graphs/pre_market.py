"""Pre-Market LangGraph — runs at 08:00 IST before market open."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from autotrader.agents.layer1.catalyst_intelligence import catalyst_intelligence_agent
from autotrader.agents.layer1.market_regime import market_regime_agent
from autotrader.agents.layer1.sector_rotation import sector_rotation_agent
from autotrader.agents.layer2.relative_strength import relative_strength_agent
from autotrader.agents.layer2.technical_structure import technical_structure_agent
from autotrader.agents.layer2.volume_intelligence import volume_intelligence_agent
from autotrader.agents.layer3.opportunity_scoring import opportunity_scoring_agent
from autotrader.agents.layer4.governance import governance_agent
from autotrader.agents.layer4.risk import risk_agent
from autotrader.agents.layer5.execution import execution_agent
from autotrader.agents.layer5.trade_construction import trade_construction_agent
from autotrader.core.state import TradingState


def _governance_router(state: TradingState) -> str:
    return "risk" if state.get("governance_approved") else END


def _risk_router(state: TradingState) -> str:
    return "trade_construction" if state.get("risk_passed") else END


def build_pre_market_graph():
    """Construct and compile the pre-market LangGraph."""
    graph = StateGraph(TradingState)

    # Register all nodes
    graph.add_node("market_regime", market_regime_agent)
    graph.add_node("sector_rotation", sector_rotation_agent)
    graph.add_node("catalyst_intelligence", catalyst_intelligence_agent)
    graph.add_node("relative_strength", relative_strength_agent)
    graph.add_node("volume_intelligence", volume_intelligence_agent)
    graph.add_node("technical_structure", technical_structure_agent)
    graph.add_node("opportunity_scoring", opportunity_scoring_agent)
    graph.add_node("governance", governance_agent)
    graph.add_node("risk", risk_agent)
    graph.add_node("trade_construction", trade_construction_agent)
    graph.add_node("execution", execution_agent)

    # Layer 1 → Layer 1 (sequential)
    graph.set_entry_point("market_regime")
    graph.add_edge("market_regime", "sector_rotation")
    graph.add_edge("sector_rotation", "catalyst_intelligence")

    # Layer 1 → Layer 2 (sequential per spec — discovery agents run independently but in sequence)
    graph.add_edge("catalyst_intelligence", "relative_strength")
    graph.add_edge("relative_strength", "volume_intelligence")
    graph.add_edge("volume_intelligence", "technical_structure")

    # Layer 2 → Layer 3
    graph.add_edge("technical_structure", "opportunity_scoring")

    # Layer 3 → Layer 4 (Governance before Risk per spec)
    graph.add_edge("opportunity_scoring", "governance")

    # Governance gate
    graph.add_conditional_edges(
        "governance",
        _governance_router,
        {"risk": "risk", END: END},
    )

    # Risk gate
    graph.add_conditional_edges(
        "risk",
        _risk_router,
        {"trade_construction": "trade_construction", END: END},
    )

    # Layer 4 → Layer 5
    graph.add_edge("trade_construction", "execution")
    graph.add_edge("execution", END)

    return graph.compile()

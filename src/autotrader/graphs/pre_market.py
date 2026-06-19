"""Pre-market analysis graph - runs before market open."""
import structlog
from langgraph.graph import StateGraph, END
from autotrader.core.state import TradingState
from autotrader.agents.layer1.market_regime import market_regime_agent
from autotrader.agents.layer1.sector_rotation import sector_rotation_agent
from autotrader.agents.layer1.catalyst_intelligence import catalyst_intelligence_agent
from autotrader.agents.layer1.options_intelligence import options_intelligence_agent
from autotrader.agents.layer2.relative_strength import relative_strength_agent
from autotrader.agents.layer2.volume_intelligence import volume_intelligence_agent
from autotrader.agents.layer2.technical_structure import technical_structure_agent
from autotrader.agents.layer3.opportunity_scoring import opportunity_scoring_agent
from autotrader.agents.layer4.governance import governance_agent
from autotrader.agents.layer4.risk import risk_agent
from autotrader.agents.layer5.trade_construction import trade_construction_agent
from autotrader.agents.layer5.execution import execution_agent

logger = structlog.get_logger()


def governance_router(state: TradingState) -> str:
    """Route based on governance approval."""
    if state.get("governance_approved"):
        return "approved"
    return "rejected"


def risk_router(state: TradingState) -> str:
    """Route based on risk check result."""
    if state.get("risk_passed"):
        return "passed"
    return "failed"


def build_pre_market_graph():
    """Build and compile the pre-market analysis graph."""
    graph = StateGraph(TradingState)
    
    # Add all nodes
    graph.add_node("market_regime", market_regime_agent)
    graph.add_node("sector_rotation", sector_rotation_agent)
    graph.add_node("catalyst_intelligence", catalyst_intelligence_agent)
    graph.add_node("options_intelligence", options_intelligence_agent)
    graph.add_node("relative_strength", relative_strength_agent)
    graph.add_node("volume_intelligence", volume_intelligence_agent)
    graph.add_node("technical_structure", technical_structure_agent)
    graph.add_node("opportunity_scoring", opportunity_scoring_agent)
    graph.add_node("governance", governance_agent)
    graph.add_node("risk", risk_agent)
    graph.add_node("trade_construction", trade_construction_agent)
    graph.add_node("execution", execution_agent)

    # Set entry point
    graph.set_entry_point("market_regime")

    # market_regime fans out to 3 parallel Layer 1 agents
    graph.add_edge("market_regime", "sector_rotation")
    graph.add_edge("market_regime", "catalyst_intelligence")
    graph.add_edge("market_regime", "options_intelligence")

    # All 3 Layer 1 agents must complete before relative_strength
    graph.add_edge("sector_rotation", "relative_strength")
    graph.add_edge("catalyst_intelligence", "relative_strength")
    graph.add_edge("options_intelligence", "relative_strength")
    graph.add_edge("relative_strength", "volume_intelligence")
    graph.add_edge("volume_intelligence", "technical_structure")
    graph.add_edge("technical_structure", "opportunity_scoring")
    graph.add_edge("opportunity_scoring", "governance")
    
    # Conditional: governance -> risk (approved) or END (rejected)
    graph.add_conditional_edges(
        "governance",
        governance_router,
        {
            "approved": "risk",
            "rejected": END,
        },
    )
    
    # Conditional: risk -> trade_construction (passed) or END (failed)
    graph.add_conditional_edges(
        "risk",
        risk_router,
        {
            "passed": "trade_construction",
            "failed": END,
        },
    )
    
    # trade_construction -> execution -> END
    graph.add_edge("trade_construction", "execution")
    graph.add_edge("execution", END)
    
    return graph.compile()

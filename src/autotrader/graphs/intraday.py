"""Intraday monitoring graph - runs during market hours."""
import structlog
from langgraph.graph import StateGraph, END
from autotrader.core.config import load_config
from autotrader.core.state import TradingState
from autotrader.agents.compete.hypothetical_monitor import compete_hypothetical_monitor_agent
from autotrader.agents.layer1.market_regime import market_regime_agent
from autotrader.agents.layer4.governance import governance_agent
from autotrader.agents.layer5.monitoring import monitoring_agent

logger = structlog.get_logger()


def should_monitor(state: TradingState) -> str:
    """Determine if monitoring should continue or if we should check for new trades."""
    positions = state.get("positions", [])
    open_positions = [p for p in positions if p.get("status", "open") == "open"]
    if open_positions:
        return "has_positions"
    return "no_positions"


def build_intraday_graph():
    """Build and compile the intraday monitoring graph."""
    cfg = load_config()
    graph = StateGraph(TradingState)

    graph.add_node("market_regime", market_regime_agent)
    graph.add_node("governance", governance_agent)
    graph.add_node("monitoring", monitoring_agent)

    graph.set_entry_point("market_regime")
    graph.add_edge("market_regime", "governance")
    graph.add_edge("governance", "monitoring")

    if cfg.compete.enabled:
        graph.add_node("compete_hypothetical_monitor", compete_hypothetical_monitor_agent)
        graph.add_edge("monitoring", "compete_hypothetical_monitor")
        graph.add_edge("compete_hypothetical_monitor", END)
    else:
        graph.add_edge("monitoring", END)

    return graph.compile()

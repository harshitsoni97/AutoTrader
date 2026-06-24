"""Intraday monitoring graph - runs during market hours."""
import structlog
from langgraph.graph import StateGraph, END
from autotrader.core.config import load_config
from autotrader.core.state import TradingState
from autotrader.agents.compete.hypothetical_monitor import compete_hypothetical_monitor_agent
from autotrader.agents.layer1.market_regime import market_regime_agent
from autotrader.agents.layer5.monitoring import monitoring_agent
from autotrader.agents.layer5.reentry import intra_reentry_agent

logger = structlog.get_logger()


def build_intraday_graph():
    """Build and compile the intraday monitoring graph."""
    cfg = load_config()
    graph = StateGraph(TradingState)

    graph.add_node("market_regime", market_regime_agent)
    graph.add_node("monitoring", monitoring_agent)
    graph.add_node("reentry", intra_reentry_agent)

    graph.set_entry_point("market_regime")
    graph.add_edge("market_regime", "monitoring")
    graph.add_edge("monitoring", "reentry")

    if cfg.compete.enabled:
        graph.add_node("compete_hypothetical_monitor", compete_hypothetical_monitor_agent)
        graph.add_edge("reentry", "compete_hypothetical_monitor")
        graph.add_edge("compete_hypothetical_monitor", END)
    else:
        graph.add_edge("reentry", END)

    return graph.compile()

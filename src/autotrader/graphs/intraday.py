"""Intraday LangGraph — runs every minute during market hours (9:15–15:30 IST)."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from autotrader.agents.layer5.monitoring import monitoring_agent
from autotrader.core.state import TradingState


def _continue_monitoring(state: TradingState) -> str:
    """Continue if there are still open positions."""
    open_positions = [p for p in state.get("positions", []) if p.get("status") == "OPEN"]
    return "monitor" if open_positions else END


def build_intraday_graph():
    """Construct and compile the intraday monitoring LangGraph."""
    graph = StateGraph(TradingState)

    graph.add_node("monitor", monitoring_agent)

    graph.set_entry_point("monitor")
    graph.add_conditional_edges(
        "monitor",
        _continue_monitoring,
        {"monitor": END, END: END},  # Single-pass per scheduler tick
    )

    return graph.compile()

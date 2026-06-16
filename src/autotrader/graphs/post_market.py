"""Post-Market LangGraph — runs at 15:45 IST after market close."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from autotrader.agents.layer6.agent_evaluator import agent_evaluator
from autotrader.agents.layer6.daily_learning import daily_learning_agent
from autotrader.agents.layer6.long_term_memory import long_term_memory_agent
from autotrader.agents.layer6.memory_compression import memory_compression_agent
from autotrader.core.state import TradingState


def _should_compress(state: TradingState) -> str:
    """Run compression weekly (Friday) or always in testing."""
    import datetime
    today = datetime.date.today()
    if today.weekday() == 4:  # Friday
        return "memory_compression"
    return END


def build_post_market_graph():
    """Construct and compile the post-market learning LangGraph."""
    graph = StateGraph(TradingState)

    graph.add_node("daily_learning", daily_learning_agent)
    graph.add_node("agent_evaluator", agent_evaluator)
    graph.add_node("long_term_memory", long_term_memory_agent)
    graph.add_node("memory_compression", memory_compression_agent)

    graph.set_entry_point("daily_learning")
    graph.add_edge("daily_learning", "agent_evaluator")
    graph.add_edge("agent_evaluator", "long_term_memory")

    # Conditional: only compress on Fridays
    graph.add_conditional_edges(
        "long_term_memory",
        _should_compress,
        {"memory_compression": "memory_compression", END: END},
    )
    graph.add_edge("memory_compression", END)

    return graph.compile()

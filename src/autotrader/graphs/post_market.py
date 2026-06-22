"""Post-market learning graph - runs after market close."""
import structlog
from langgraph.graph import StateGraph, END
from autotrader.core.config import load_config
from autotrader.core.state import TradingState
from autotrader.agents.compete.evaluator import compete_evaluator_agent
from autotrader.agents.layer6.daily_learning import daily_learning_agent
from autotrader.agents.layer6.agent_evaluator import agent_evaluator
from autotrader.agents.layer6.long_term_memory import long_term_memory_agent
from autotrader.agents.layer6.memory_compression import memory_compression_agent

logger = structlog.get_logger()


def build_post_market_graph():
    """Build and compile the post-market analysis graph."""
    cfg = load_config()
    graph = StateGraph(TradingState)

    graph.add_node("daily_learning", daily_learning_agent)
    graph.add_node("agent_evaluator", agent_evaluator)
    graph.add_node("long_term_memory", long_term_memory_agent)
    graph.add_node("memory_compression", memory_compression_agent)

    graph.set_entry_point("daily_learning")
    graph.add_edge("daily_learning", "agent_evaluator")

    if cfg.compete.enabled:
        graph.add_node("compete_evaluator", compete_evaluator_agent)
        graph.add_edge("agent_evaluator", "compete_evaluator")
        graph.add_edge("compete_evaluator", "long_term_memory")
    else:
        graph.add_edge("agent_evaluator", "long_term_memory")

    graph.add_edge("long_term_memory", "memory_compression")
    graph.add_edge("memory_compression", END)

    return graph.compile()

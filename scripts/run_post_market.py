#!/usr/bin/env python3
"""Post-market learning run — scheduled at 15:45 IST.

Usage:
    python scripts/run_post_market.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.core.state import create_initial_state
from autotrader.graphs.post_market import build_post_market_graph
from autotrader.safety.controls import SafetyControls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== AutoTrader Post-Market Learning Run ===")

    safety = SafetyControls()
    # Post-market runs even on holidays (for learning from prior day data)
    ok, _ = safety.check_kill_switch()
    if not ok:
        logger.error("Kill switch active — post-market run aborted")
        return 1

    # In production: load today's full state from persistent store
    state = create_initial_state(session_type="post_market")

    graph = build_post_market_graph()
    result = graph.invoke(state)

    logger.info("Daily Learning Report: %s", result.get("learning_report_path", "not saved"))
    logger.info("Agent Scores: %s", result.get("agent_scores", {}))
    logger.info("Audit trail entries: %d", len(result.get("audit_trail", [])))
    logger.info("=== Post-Market Run Complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Intraday monitoring — runs every minute during market hours (9:15–15:30 IST).

In production this is called by the scheduler every 60 seconds.
It receives the live state (positions, orders) from a persistent store.

Usage:
    python scripts/run_intraday.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.core.state import create_initial_state
from autotrader.graphs.intraday import build_intraday_graph
from autotrader.safety.controls import SafetyControls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== AutoTrader Intraday Monitoring Tick ===")

    safety = SafetyControls()
    ok, issues = safety.run_all_checks_basic()
    if not ok:
        for issue in issues:
            logger.error("Safety: %s", issue)
        return 1

    # In production: load live state from Redis/Postgres
    # Here we use a fresh state (no positions) as demo
    state = create_initial_state(session_type="intraday")

    graph = build_intraday_graph()
    result = graph.invoke(state)

    open_positions = [p for p in result.get("positions", []) if p.get("status") == "OPEN"]
    logger.info("Open positions: %d | Daily PnL: %.0f INR", len(open_positions), result.get("daily_pnl", 0))

    for alert in result.get("errors", []):
        logger.warning("ALERT: %s", alert)

    return 0


if __name__ == "__main__":
    sys.exit(main())

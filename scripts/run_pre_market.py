#!/usr/bin/env python3
"""Pre-market run — scheduled at 08:00 IST.

Usage:
    python scripts/run_pre_market.py
"""

import logging
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autotrader.core.config import load_config
from autotrader.core.state import create_initial_state
from autotrader.graphs.pre_market import build_pre_market_graph
from autotrader.reports.generators import save_all_reports
from autotrader.safety.controls import SafetyControls

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    logger.info("=== AutoTrader Pre-Market Run Starting ===")

    # Safety pre-flight
    safety = SafetyControls()
    ok, issues = safety.run_all_checks_basic()
    if not ok:
        for issue in issues:
            logger.error("Safety check failed: %s", issue)
        logger.error("Pre-market run ABORTED due to safety checks")
        return 1

    cfg = load_config()
    logger.info("Config loaded: capital=%.0f, min_score=%.0f", cfg.trading_policy.total_capital, cfg.trading_policy.minimum_score)

    # Build initial state
    state = create_initial_state(session_type="pre_market")

    # Build and run graph
    graph = build_pre_market_graph()
    logger.info("Running pre-market analysis graph...")
    result = graph.invoke(state)

    # Report outcomes
    logger.info("--- PRE-MARKET RESULTS ---")
    logger.info("Market Regime: %s (confidence=%.2f)", result.get("market_regime"), result.get("market_confidence"))
    logger.info("Top Sectors: %s", result.get("top_sectors"))
    logger.info("Catalysts Found: %d", len(result.get("catalysts", [])))
    logger.info("Candidates: %d", len(result.get("candidates", [])))
    logger.info("Scored Opportunities: %d", len(result.get("scored_opportunities", [])))
    logger.info("Governance: %s — %s", result.get("governance_approved"), result.get("governance_reason"))
    logger.info("Risk: %s — %s", result.get("risk_passed"), result.get("risk_reason"))

    if result.get("trade_plan"):
        tp = result["trade_plan"]
        logger.info("Trade Plan: %s Entry=%.2f Stop=%.2f T1=%.2f T2=%.2f Qty=%d",
                    tp.get("symbol"), tp.get("entry", 0), tp.get("stop", 0),
                    tp.get("target1", 0), tp.get("target2", 0), tp.get("qty", 0))

    if result.get("orders"):
        for order in result["orders"]:
            logger.info("Order: %s %s x%d @ %.2f [%s]",
                        order.get("order_id"), order.get("symbol"), order.get("qty", 0),
                        order.get("fill_price", 0), order.get("status"))

    # Save reports
    paths = save_all_reports(result, "reports")
    for report_type, path in paths.items():
        logger.info("Report saved: %s → %s", report_type, path)

    if result.get("errors"):
        for err in result["errors"]:
            logger.warning("Error: %s", err)

    logger.info("=== Pre-Market Run Complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

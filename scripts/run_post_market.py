#!/usr/bin/env python3
"""Run post-market analysis and learning. Scheduled after 15:30 IST."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import structlog
from autotrader.graphs.post_market import build_post_market_graph
from autotrader.core.config import load_config
from autotrader.core.tracing import setup_tracing
from autotrader.core.state import create_initial_state
from autotrader.safety.controls import SafetyControls
from autotrader.reports.generators import (
    generate_daily_trade_report,
    generate_agent_diagnostic_report,
    generate_a2a_learning_report,
    generate_governance_report,
    save_report,
)

logger = structlog.get_logger()


def main():
    """Run the post-market analysis and learning pipeline."""
    logger.info("post_market_starting")
    
    try:
        config = load_config()
        logger.info("config_loaded")
        setup_tracing(config)
    except Exception as e:
        logger.error("config_load_failed", error=str(e))
        sys.exit(1)
    
    safety = SafetyControls()
    ok, issues = safety.run_all_checks_basic()
    if not ok:
        logger.warning("safety_checks_issues", issues=issues)
        # Post-market runs even if market is closed (expected)
    
    # In production, this state would be loaded from the intraday session
    state = create_initial_state(session_type="post_market")
    graph = build_post_market_graph()
    
    logger.info("starting_post_market_analysis")
    try:
        result = graph.invoke(state)
        logger.info("post_market_complete")
    except Exception as e:
        logger.error("post_market_graph_failed", error=str(e))
        raise
    
    # Generate all reports
    run_date = result.get("run_date", "unknown")
    
    trade_report = generate_daily_trade_report(result)
    save_report(trade_report, f"{run_date}_post_market_trade_report.md")
    
    diag_report = generate_agent_diagnostic_report(result)
    save_report(diag_report, f"{run_date}_agent_diagnostic_report.md")
    
    a2a_report = generate_a2a_learning_report(result)
    save_report(a2a_report, f"{run_date}_a2a_learning_report.md")
    
    gov_report = generate_governance_report(result)
    save_report(gov_report, f"{run_date}_governance_report.md")
    
    logger.info("all_reports_saved", run_date=run_date)
    print(f"\nPost-market analysis complete for {run_date}.")
    print("Reports saved to reports/ directory.")
    
    return result


if __name__ == "__main__":
    main()

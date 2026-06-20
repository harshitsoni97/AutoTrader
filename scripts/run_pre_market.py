#!/usr/bin/env python3
"""Run pre-market analysis. Scheduled at 08:00 IST."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Load .env from project root before anything else
from pathlib import Path
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=True)
    except ImportError:
        # Parse manually if python-dotenv not installed
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

import structlog

from autotrader.graphs.pre_market import build_pre_market_graph
from autotrader.core.config import load_config
from autotrader.core.state import create_initial_state
from autotrader.core.tracing import setup_tracing
from autotrader.safety.controls import SafetyControls
from autotrader.reports.generators import generate_daily_trade_report, save_report

logger = structlog.get_logger()


def main():
    """Run the pre-market analysis pipeline."""
    logger.info("pre_market_starting")
    
    # Load configuration
    try:
        config = load_config()
        logger.info("config_loaded", strategy_version=config.strategy_version.strategy_version)
        setup_tracing(config)
    except Exception as e:
        logger.error("config_load_failed", error=str(e))
        sys.exit(1)
    
    # Safety checks — holiday/weekend is a warning only for pre-market analysis.
    # Kill switch and API health are hard stops.
    safety = SafetyControls()
    ok, issues = safety.run_all_checks_basic()
    if not ok:
        holiday_only = all("holiday or weekend" in i for i in issues)
        if holiday_only:
            logger.warning("safety_checks_warning_weekend", issues=issues)
            print(f"Warning (running anyway): {issues}")
        else:
            logger.error("safety_checks_failed", issues=issues)
            print(f"Safety checks failed: {issues}")
            return
    
    logger.info("safety_checks_passed")
    
    # Initialize state
    state = create_initial_state(session_type="pre_market")
    
    # Build and run graph
    graph = build_pre_market_graph()
    
    logger.info("starting_pre_market_analysis")
    try:
        result = graph.invoke(state)
        logger.info(
            "pre_market_complete",
            regime=result.get("market_regime"),
            governance=result.get("governance_approved"),
            risk=result.get("risk_passed"),
            trades=result.get("daily_trades_taken", 0),
        )
    except Exception as e:
        logger.error("pre_market_graph_failed", error=str(e))
        from autotrader.tools.notifications import get_notifier
        get_notifier(config.notifications).notify_error("pre_market", str(e))
        raise

    # Generate and save report
    report = generate_daily_trade_report(result)
    report_filename = f"{result.get('run_date', 'unknown')}_pre_market_report.md"
    path = save_report(report, report_filename)
    logger.info("pre_market_report_saved", report_path=path)

    # Notify pre-market summary (regime + go/no-go) if configured.
    from autotrader.tools.notifications import get_notifier
    get_notifier(config.notifications).notify_daily_summary({
        "run_date": result.get("run_date", "unknown"),
        "dry_run": result.get("dry_run", config.trading_policy.dry_run),
        "trades": result.get("daily_trades_taken", 0),
        "daily_pnl": round(result.get("daily_pnl", 0.0), 2),
        "regime": result.get("market_regime", "n/a"),
    })

    print(f"\nPre-market analysis complete.")
    print(f"Market Regime: {result.get('market_regime')}")
    print(f"Governance Approved: {result.get('governance_approved')}")
    print(f"Risk Passed: {result.get('risk_passed')}")
    print(f"Report saved to: {path}")
    
    return result


if __name__ == "__main__":
    main()

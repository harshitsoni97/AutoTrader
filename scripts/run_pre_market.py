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
from autotrader.graphs.compete import build_compete_graph
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
    
    # Safety checks. On a market holiday/weekend we HARD-STOP: there is no
    # market data, so simulating trades only pollutes the learning/RL data.
    # Kill switch and API health are also hard stops.
    safety = SafetyControls()
    ok, issues = safety.run_all_checks_basic()
    if not ok:
        holiday_only = all("holiday or weekend" in i for i in issues)
        if holiday_only:
            from datetime import date as _date
            logger.info("market_closed_skipping_pre_market", issues=issues)
            print(f"Market closed today — skipping pre-market. {issues}")
            try:
                from autotrader.tools.notifications import get_notifier
                get_notifier(config.notifications).send(
                    subject=f"Market Closed — {_date.today().isoformat()}",
                    body=(
                        f"🔴 NSE is closed today ({_date.today().isoformat()}).\n"
                        f"Reason: {issues[0] if issues else 'holiday or weekend'}\n"
                        "Pre-market analysis skipped — no trades will be placed."
                    ),
                )
            except Exception as exc:
                logger.warning("market_closed_notify_failed", error=str(exc))
            return
        else:
            logger.error("safety_checks_failed", issues=issues)
            print(f"Safety checks failed: {issues}")
            return
    
    logger.info("safety_checks_passed")
    
    # Initialize state
    state = create_initial_state(session_type="pre_market")
    
    # Build and run graph — use compete graph when compete mode is enabled
    if config.compete.enabled:
        logger.info("compete_mode_enabled", stacks=[s.name for s in config.compete.stacks])
        graph = build_compete_graph()
    else:
        graph = build_pre_market_graph()
    
    logger.info("starting_pre_market_analysis")
    try:
        result = graph.invoke(state)
        compete_dry_run = config.compete.enabled and config.compete.dry_run
        logger.info(
            "pre_market_complete",
            regime=result.get("market_regime"),
            governance="compete_dry_run" if compete_dry_run else result.get("governance_approved"),
            risk="compete_dry_run" if compete_dry_run else result.get("risk_passed"),
            trades=result.get("daily_trades_taken", 0),
        )
    except Exception as e:
        logger.error("pre_market_graph_failed", error=str(e))
        from autotrader.tools.notifications import get_notifier
        get_notifier(config.notifications).notify_error("pre_market", str(e))
        raise

    # Pre-flight: warn if any scored/traded symbol is missing from the Upstox
    # instrument map. A missing symbol silently yields ₹0 P&L (no price lookup),
    # so surface it loudly on Slack instead of letting it pass unnoticed.
    try:
        from autotrader.agents.layer2.technical_structure import _load_instrument_map
        imap = _load_instrument_map()
        check_symbols = {o.get("symbol") for o in result.get("scored_opportunities", [])}
        check_symbols |= {p.get("symbol") for p in result.get("positions", [])}
        missing = sorted(s for s in check_symbols if s and s not in imap)
        if missing:
            logger.warning("symbols_missing_from_instrument_map", missing=missing,
                           map_size=len(imap))
            from autotrader.tools.notifications import get_notifier
            get_notifier(config.notifications).send(
                subject=f"⚠️ Instrument map missing {len(missing)} symbol(s)",
                body=(
                    "These symbols are not in config/upstox_instruments.json, so "
                    "they will get NO live price and ₹0 P&L:\n"
                    f"{', '.join(missing)}\n\n"
                    "Fix: run `python3 scripts/update_instruments.py` to refresh "
                    f"the map (currently {len(imap)} symbols)."
                ),
            )
    except Exception as exc:
        logger.warning("instrument_map_preflight_failed", error=str(exc))

    # Persist session state so post-market can compute dry-run assumed P&L
    from autotrader.core.session_store import save_session
    save_session(result)

    # Generate and save report
    report = generate_daily_trade_report(result)
    report_filename = f"{result.get('run_date', 'unknown')}_pre_market_report.md"
    path = save_report(report, report_filename)
    logger.info("pre_market_report_saved", report_path=path)

    from autotrader.tools.notifications import get_notifier
    notifier = get_notifier(config.notifications)

    # Notify compete stack picks if compete mode is enabled.
    competitor_results = result.get("competitor_results", [])
    if competitor_results:
        notifier.notify_compete_summary(
            competitor_results,
            run_date=result.get("run_date", "unknown"),
            dry_run=result.get("dry_run", config.trading_policy.dry_run),
            trade_plan=result.get("trade_plan", {}),
        )

    print(f"\nPre-market analysis complete.")
    print(f"Market Regime: {result.get('market_regime')}")
    if config.compete.enabled and config.compete.dry_run:
        print("Mode: Compete DRY RUN (governance/risk skipped — no live execution)")
    else:
        print(f"Governance Approved: {result.get('governance_approved')}")
        print(f"Risk Passed: {result.get('risk_passed')}")
    print(f"Report saved to: {path}")
    
    return result


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run intraday monitoring loop. Runs every 5 minutes during market hours."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from pathlib import Path
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

import time
import structlog
from datetime import datetime, timezone, timedelta
from autotrader.graphs.intraday import build_intraday_graph
from autotrader.core.config import load_config
from autotrader.core.tracing import setup_tracing

from autotrader.core.state import create_initial_state
from autotrader.safety.controls import SafetyControls

logger = structlog.get_logger()

# NSE market hours IST: 09:15 - 15:30
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
IST_OFFSET = timedelta(hours=5, minutes=30)
POLL_INTERVAL_SECONDS = 300  # 5 minutes


def is_market_open() -> bool:
    """Check if NSE market is currently open (IST)."""
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFFSET
    
    if now_ist.weekday() >= 5:  # Weekend
        return False
    
    market_open = now_ist.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now_ist.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    
    return market_open <= now_ist <= market_close


def main():
    """Run the intraday monitoring loop."""
    logger.info("intraday_monitoring_starting")
    
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
        logger.error("safety_checks_failed", issues=issues)
        print(f"Safety checks failed: {issues}")
        return
    
    # Initialize state (would normally carry over from pre-market run)
    state = create_initial_state(session_type="intraday")
    graph = build_intraday_graph()
    
    iteration = 0
    while True:
        if not is_market_open():
            now_ist = datetime.now(timezone.utc) + IST_OFFSET
            logger.info("market_closed", time_ist=now_ist.strftime("%H:%M:%S"))
            print(f"Market closed at IST {now_ist.strftime('%H:%M:%S')}. Exiting intraday loop.")
            break
        
        iteration += 1
        logger.info("intraday_iteration", iteration=iteration)
        
        try:
            result = graph.invoke(state)
            # Update state with monitoring results for next iteration
            state.update({
                "market_regime": result.get("market_regime", state.get("market_regime")),
                "market_confidence": result.get("market_confidence", state.get("market_confidence")),
                "positions": result.get("positions", state.get("positions", [])),
                "orders": result.get("orders", state.get("orders", [])),
                "daily_pnl": result.get("daily_pnl", state.get("daily_pnl", 0.0)),
                "consecutive_losses": result.get("consecutive_losses", state.get("consecutive_losses", 0)),
            })
            
            open_positions = [p for p in state.get("positions", []) if p.get("status") == "open"]
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Iteration {iteration} — Regime: {state.get('market_regime')} | "
                f"Open: {len(open_positions)} | P&L: ₹{state.get('daily_pnl', 0):.2f}"
            )
        except Exception as e:
            logger.error("intraday_iteration_failed", iteration=iteration, error=str(e))
        
        time.sleep(POLL_INTERVAL_SECONDS)
    
    logger.info("intraday_monitoring_complete", iterations=iteration)
    return state


if __name__ == "__main__":
    main()

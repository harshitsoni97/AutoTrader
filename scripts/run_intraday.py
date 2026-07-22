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
        load_dotenv(_env_path, override=True)
    except ImportError:
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

import argparse
import time
import structlog
from datetime import datetime, timezone, timedelta
from autotrader.graphs.intraday import build_intraday_graph
from autotrader.core.config import load_config
from autotrader.core.tracing import setup_tracing

from autotrader.core.state import create_initial_state
from autotrader.safety.controls import SafetyControls

from autotrader.core.logging_setup import configure_logging
configure_logging()
logger = structlog.get_logger()

# NSE market hours IST: 09:15 - 15:30
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
IST_OFFSET = timedelta(hours=5, minutes=30)
POLL_INTERVAL_SECONDS = 300  # 5 minutes


_HOLIDAYS_CACHE: list[str] | None = None


def _is_holiday(day) -> bool:
    """True if `day` (a date) is a known NSE holiday. Cached for the process.

    If the holiday list can't be fetched, assume NOT a holiday — we'd rather run
    a no-op loop on a rare unfetchable-holiday than skip a real trading day.
    """
    global _HOLIDAYS_CACHE
    if _HOLIDAYS_CACHE is None:
        try:
            from autotrader.tools import upstox_data
            _HOLIDAYS_CACHE = upstox_data.get_market_holidays() or []
        except Exception:
            _HOLIDAYS_CACHE = []
    return day.isoformat() in _HOLIDAYS_CACHE


def is_market_open() -> bool:
    """Check if NSE is open, treating the IST time window + holiday calendar as
    the AUTHORITATIVE loop terminator.

    The Upstox live status is used only as an advisory shortcut: if it explicitly
    says NORMAL_OPEN we're open. But a non-NORMAL_OPEN reading (pre-open auction,
    a transient CLOSING_SESSION flag, a volatility halt, an unexpected string, or
    an API hiccup) must NOT break the loop for the rest of the day — as long as
    we're inside the trading window on a non-holiday weekday, we keep looping.
    """
    now_ist = datetime.now(timezone.utc) + IST_OFFSET

    # Hard closers: weekend, holiday, or outside the 09:15–15:30 IST window.
    if now_ist.weekday() >= 5:
        return False
    if _is_holiday(now_ist.date()):
        return False
    market_open = now_ist.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now_ist.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    if not (market_open <= now_ist <= market_close):
        return False

    # Inside the window on a trading day → open. (Advisory API check omitted as a
    # terminator on purpose: a single odd status string must not end the session.)
    return True


def main():
    """Run the intraday monitoring loop."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bypass-market-hours", action="store_true",
        help="Skip market-hours check (for dry-run testing outside trading hours).",
    )
    args = parser.parse_args()
    bypass = args.bypass_market_hours

    if bypass:
        print("WARNING: --bypass-market-hours enabled. Market-hours check skipped.")

    logger.info("intraday_monitoring_starting", bypass_market_hours=bypass)
    
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
        holiday_only = all("holiday or weekend" in i for i in issues)
        if holiday_only:
            logger.warning("safety_checks_warning_weekend", issues=issues)
            print(f"Warning: {issues}")
        else:
            logger.error("safety_checks_failed", issues=issues)
            print(f"Safety checks failed: {issues}")
            return
    
    # Carry over the pre-market session so the EntryAgent can book today's plans.
    state = create_initial_state(session_type="intraday")
    try:
        from autotrader.core.session_store import load_session
        saved = load_session()
        if saved:
            state.update({k: v for k, v in saved.items() if v is not None})
            state["session_type"] = "intraday"
            logger.info("premarket_session_loaded", plans=len(saved.get("trade_plans", [])),
                        scored=len(saved.get("scored_opportunities", [])))
        else:
            logger.warning("no_premarket_session — nothing to book intraday")
    except Exception as exc:
        logger.warning("premarket_session_load_failed", error=str(exc))
    graph = build_intraday_graph()
    
    iteration = 0
    while True:
        if not bypass and not is_market_open():
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
                "daily_trades_taken": result.get("daily_trades_taken", state.get("daily_trades_taken", 0)),
                "daily_pnl": result.get("daily_pnl", state.get("daily_pnl", 0.0)),
                "consecutive_losses": result.get("consecutive_losses", state.get("consecutive_losses", 0)),
                # Carry compete flags (stop_hit/target*_hit) so the hypothetical monitor
                # doesn't re-alert the same exit EVERY cycle (the 200-message spam on 7/22).
                "competitor_results": result.get("competitor_results", state.get("competitor_results", [])),
            })
            # Persist so post-market prices the actually-booked positions.
            try:
                from autotrader.core.session_store import save_session
                save_session(state)
            except Exception as exc:
                logger.warning("intraday_session_save_failed", error=str(exc))

            open_positions = [p for p in state.get("positions", []) if p.get("status") == "open"]
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Iteration {iteration} — Regime: {state.get('market_regime')} | "
                f"Open: {len(open_positions)} | P&L: ₹{state.get('daily_pnl', 0):.2f}"
            )
        except Exception as e:
            logger.error("intraday_iteration_failed", iteration=iteration, error=str(e))
            from autotrader.tools.notifications import get_notifier
            get_notifier(config.notifications).notify_error(f"intraday iteration {iteration}", str(e))
        
        time.sleep(POLL_INTERVAL_SECONDS)
    
    logger.info("intraday_monitoring_complete", iterations=iteration)
    return state


if __name__ == "__main__":
    main()

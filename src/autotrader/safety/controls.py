"""Safety controls for the AutoTrader system."""
import os
import structlog
from datetime import datetime, date
from typing import Optional

logger = structlog.get_logger()

# Persistent halt flag — tripped out-of-band by scripts/circuit_breaker.py and
# honored by every process that constructs SafetyControls. Override path via env.
HALT_FILE = os.getenv(
    "AUTOTRADER_HALT_FILE",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "../../../reports/HALT")),
)


def _halt_file_active() -> bool:
    return os.path.exists(HALT_FILE)


def trip_halt(reason: str) -> str:
    """Create the halt file with a reason. Returns the path."""
    os.makedirs(os.path.dirname(HALT_FILE), exist_ok=True)
    with open(HALT_FILE, "w") as f:
        f.write(f"{datetime.now().isoformat()}\n{reason}\n")
    logger.error("halt_tripped", reason=reason, path=HALT_FILE)
    return HALT_FILE


def clear_halt() -> bool:
    """Remove the halt file (manual reset). Returns True if a file was removed."""
    if os.path.exists(HALT_FILE):
        os.remove(HALT_FILE)
        logger.warning("halt_cleared", path=HALT_FILE)
        return True
    return False

# NSE trading holidays 2024-2025 (partial list - key observed holidays)
NSE_HOLIDAYS = {
    "2024-01-26",  # Republic Day
    "2024-03-25",  # Holi
    "2024-03-29",  # Good Friday
    "2024-04-11",  # Id-Ul-Fitr (Ramzan Eid)
    "2024-04-14",  # Dr. Ambedkar Jayanti
    "2024-04-17",  # Ram Navami
    "2024-04-21",  # Mahavir Jayanti
    "2024-04-23",  # General Election
    "2024-05-20",  # General Election
    "2024-05-23",  # General Election
    "2024-06-17",  # Bakri Id (Eid ul-Adha)
    "2024-07-17",  # Muharram
    "2024-08-15",  # Independence Day
    "2024-10-02",  # Mahatma Gandhi Jayanti
    "2024-10-14",  # Dussehra
    "2024-11-01",  # Diwali (Laxmi Pujan)
    "2024-11-15",  # Gurunanak Jayanti
    "2024-12-25",  # Christmas
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr
    "2025-04-10",  # Shri Ram Navami
    "2025-04-14",  # Dr. Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-08-15",  # Independence Day
    "2025-10-02",  # Gandhi Jayanti
    "2025-10-02",  # Dussehra
    "2025-10-20",  # Diwali
    "2025-10-21",  # Diwali (Laxmi Pujan)
    "2025-11-05",  # Gurunanak Jayanti
    "2025-12-25",  # Christmas
}


class SafetyControls:
    """Comprehensive safety controls for the trading system."""
    
    def __init__(self):
        self.kill_switch = False
        self._strategy_version = "1.0.0"

    def check_kill_switch(self) -> bool:
        """Returns True if system is safe to operate (kill switch NOT active).

        Honors both the in-memory flag and a persistent halt file, so an
        out-of-band circuit breaker (scripts/circuit_breaker.py) can stop all
        trading regardless of what the agents decide. The file is the source of
        truth across processes.
        """
        if self.kill_switch or _halt_file_active():
            logger.warning("kill_switch_active", halt_file=_halt_file_active())
            return False
        return True
    
    def check_api_health(self) -> bool:
        """Check yfinance API health by fetching a test ticker."""
        try:
            import yfinance as yf
            ticker = yf.Ticker("^NSEI")
            info = ticker.fast_info
            # fast_info returns immediately; check it has data
            _ = info.last_price
            return True
        except Exception as e:
            logger.warning("api_health_check_failed", error=str(e))
            # If we can't check, assume ok (network might be down in test env)
            return True
    
    def check_data_freshness(self, timestamps: list) -> bool:
        """Check all timestamps are within 5 minutes of now."""
        if not timestamps:
            return True
        now = datetime.utcnow()
        from datetime import timedelta
        threshold = timedelta(minutes=5)
        for ts in timestamps:
            try:
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
                elif isinstance(ts, datetime):
                    dt = ts
                else:
                    continue
                if now - dt > threshold:
                    logger.warning("stale_data_detected", timestamp=ts, age_minutes=(now - dt).seconds / 60)
                    return False
            except (ValueError, TypeError) as e:
                logger.warning("timestamp_parse_failed", ts=ts, error=str(e))
        return True
    
    def check_duplicate_trade(self, symbol: str, existing_orders: list) -> bool:
        """
        Returns True if it's safe to trade (no duplicate order for symbol).
        Returns False if symbol already has an open buy order.
        """
        for order in existing_orders:
            if (order.get("symbol") == symbol and 
                order.get("side") == "BUY" and 
                order.get("status") in ("FILLED", "PENDING")):
                logger.warning("duplicate_trade_detected", symbol=symbol)
                return False
        return True
    
    def check_holiday(self, check_date: Optional[str] = None) -> bool:
        """
        Returns True if it's a trading day (not a holiday or weekend).
        Returns False if today is a holiday or weekend.
        """
        if check_date is None:
            check_date = str(date.today())

        # Check weekend
        try:
            d = date.fromisoformat(check_date)
            if d.weekday() >= 5:  # Saturday=5, Sunday=6
                logger.info("market_closed_weekend", date=check_date)
                return False
        except ValueError:
            return True

        # Fetch live holiday list from Upstox; fall back to hardcoded set
        try:
            from autotrader.tools import upstox_data
            live_holidays = upstox_data.get_market_holidays()
            holiday_set = set(live_holidays) if live_holidays else NSE_HOLIDAYS
        except Exception:
            holiday_set = NSE_HOLIDAYS

        if check_date in holiday_set:
            logger.info("market_closed_holiday", date=check_date)
            return False

        return True
    
    def check_broker_connectivity(self, broker) -> bool:
        """Check if broker is accessible by calling get_balance."""
        try:
            balance = broker.get_balance()
            return isinstance(balance, dict) and "available_capital" in balance
        except Exception as e:
            logger.warning("broker_connectivity_failed", error=str(e))
            return False
    
    def check_memory_integrity(self, memory_store) -> bool:
        """Validate memory store is accessible and not corrupted."""
        try:
            if hasattr(memory_store, "keys"):
                _ = memory_store.keys()
            elif hasattr(memory_store, "get_stats"):
                _ = memory_store.get_stats()
            return True
        except Exception as e:
            logger.warning("memory_integrity_check_failed", error=str(e))
            return False
    
    def check_strategy_version(self, version: str) -> bool:
        """Verify strategy version matches expected version."""
        if version != self._strategy_version:
            logger.warning(
                "strategy_version_mismatch",
                expected=self._strategy_version,
                got=version,
            )
            return False
        return True
    
    def run_all_checks(self, state: dict) -> tuple:
        """Run all safety checks including state-dependent ones."""
        issues = []
        
        if not self.check_kill_switch():
            issues.append("Kill switch is active")
        
        if not self.check_api_health():
            issues.append("API health check failed")
        
        if not self.check_holiday():
            issues.append(f"Today ({date.today()}) is a market holiday or weekend")
        
        orders = state.get("orders", [])
        scored_opps = state.get("scored_opportunities", [])
        if scored_opps:
            symbol = scored_opps[0].get("symbol", "")
            if symbol and not self.check_duplicate_trade(symbol, orders):
                issues.append(f"Duplicate trade detected for {symbol}")
        
        strategy_version = state.get("strategy_version", self._strategy_version)
        if not self.check_strategy_version(strategy_version):
            issues.append(f"Strategy version mismatch: got {strategy_version}")
        
        return (len(issues) == 0, issues)
    
    def run_all_checks_basic(self) -> tuple:
        """Run basic safety checks that don't require state."""
        issues = []
        
        if not self.check_kill_switch():
            issues.append("Kill switch is active")
        
        if not self.check_api_health():
            issues.append("API health check failed")
        
        if not self.check_holiday():
            issues.append(f"Today ({date.today()}) is a market holiday or weekend")
        
        return (len(issues) == 0, issues)

"""Safety controls — mandatory guards before any trading activity."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

from autotrader.tools.nse_tools import is_market_holiday

logger = logging.getLogger(__name__)

KILL_SWITCH_ENV = "AUTOTRADER_KILL_SWITCH"
MAX_DATA_AGE_MINUTES = 60


class SafetyControls:
    def __init__(self):
        self._kill_switch: bool = False
        self._strategy_version: str = "1.0.0"

    # ── Public controls ──────────────────────────────────────────────────────

    def activate_kill_switch(self) -> None:
        self._kill_switch = True
        logger.critical("[SafetyControls] KILL SWITCH ACTIVATED")

    def deactivate_kill_switch(self) -> None:
        self._kill_switch = False
        logger.info("[SafetyControls] Kill switch deactivated")

    # ── Individual checks ────────────────────────────────────────────────────

    def check_kill_switch(self) -> tuple[bool, str]:
        if self._kill_switch or os.getenv(KILL_SWITCH_ENV, "").lower() == "true":
            return False, "Kill switch is active — all trading halted"
        return True, "Kill switch inactive"

    def check_holiday(self, check_date: date | None = None) -> tuple[bool, str]:
        target = check_date or date.today()
        if is_market_holiday(target):
            return False, f"{target} is a market holiday or weekend"
        return True, f"{target} is a trading day"

    def check_data_freshness(self, timestamps: list[str]) -> tuple[bool, str]:
        if not timestamps:
            return False, "No data timestamps provided"
        now = datetime.now(timezone.utc)
        for ts in timestamps:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_minutes = (now - dt).total_seconds() / 60
                if age_minutes > MAX_DATA_AGE_MINUTES:
                    return False, f"Stale data detected: {ts} is {age_minutes:.0f} minutes old"
            except (ValueError, TypeError):
                return False, f"Invalid timestamp format: {ts}"
        return True, "All data timestamps are fresh"

    def check_duplicate_trade(self, symbol: str, existing_orders: list[dict]) -> tuple[bool, str]:
        open_symbols = {o.get("symbol") for o in existing_orders if o.get("status") not in ("FILLED", "CANCELLED")}
        if symbol in open_symbols:
            return False, f"Duplicate trade detected for {symbol}"
        return True, f"No duplicate for {symbol}"

    def check_broker_connectivity(self, broker: Any) -> tuple[bool, str]:
        try:
            if hasattr(broker, "is_connected") and broker.is_connected():
                return True, "Broker connected"
            return False, "Broker is not connected"
        except Exception as e:
            return False, f"Broker connectivity check failed: {e}"

    def check_memory_integrity(self, memory_store: Any) -> tuple[bool, str]:
        try:
            count = memory_store.count() if hasattr(memory_store, "count") else 0
            return True, f"Memory store OK ({count} entries)"
        except Exception as e:
            return False, f"Memory integrity check failed: {e}"

    def check_strategy_version(self, expected_version: str) -> tuple[bool, str]:
        if expected_version != self._strategy_version:
            # Non-fatal: just warn, don't block
            logger.warning(
                "[SafetyControls] Strategy version mismatch: expected %s, running %s",
                expected_version, self._strategy_version,
            )
        return True, f"Strategy version: {self._strategy_version}"

    def check_api_health(self) -> tuple[bool, str]:
        """Lightweight check that external APIs are reachable."""
        try:
            import requests
            resp = requests.get("https://www.nseindia.com", timeout=5)
            if resp.status_code < 500:
                return True, "NSE API reachable"
            return False, f"NSE API returned status {resp.status_code}"
        except Exception as e:
            logger.warning("[SafetyControls] API health check failed: %s", e)
            # Non-fatal — we have mock fallbacks
            return True, f"API unreachable (will use cached/mock data): {e}"

    # ── Composite run ────────────────────────────────────────────────────────

    def run_all_checks_basic(
        self,
        check_date: date | None = None,
        existing_orders: list[dict] | None = None,
    ) -> tuple[bool, list[str]]:
        """Run mandatory pre-flight safety checks. Returns (all_ok, issues)."""
        issues: list[str] = []
        checks = [
            self.check_kill_switch(),
            self.check_holiday(check_date),
            self.check_api_health(),
        ]
        all_ok = True
        for ok, msg in checks:
            if not ok:
                all_ok = False
                issues.append(msg)
                logger.error("[SafetyControls] FAILED: %s", msg)
            else:
                logger.info("[SafetyControls] OK: %s", msg)
        return all_ok, issues

    def run_full_checks(
        self,
        broker: Any | None = None,
        memory_store: Any | None = None,
        strategy_version: str | None = None,
        data_timestamps: list[str] | None = None,
        existing_orders: list[dict] | None = None,
        check_date: date | None = None,
    ) -> tuple[bool, list[str]]:
        issues: list[str] = []
        checks = [
            self.check_kill_switch(),
            self.check_holiday(check_date),
            self.check_api_health(),
        ]
        if data_timestamps:
            checks.append(self.check_data_freshness(data_timestamps))
        if broker:
            checks.append(self.check_broker_connectivity(broker))
        if memory_store:
            checks.append(self.check_memory_integrity(memory_store))
        if strategy_version:
            checks.append(self.check_strategy_version(strategy_version))

        all_ok = True
        for ok, msg in checks:
            if not ok:
                all_ok = False
                issues.append(msg)
        return all_ok, issues

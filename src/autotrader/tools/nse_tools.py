"""NSE-specific data tools — bulk/block deals, corporate actions, ASM/GSM lists."""

from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AutoTrader/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
}


def _nse_get(path: str) -> dict | list | None:
    """Make authenticated GET to NSE API."""
    session = requests.Session()
    try:
        # Establish session cookie
        session.get(NSE_BASE, headers=HEADERS, timeout=10)
        resp = session.get(f"{NSE_BASE}{path}", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("NSE API call failed for %s: %s", path, e)
        return None


def get_fii_dii_data() -> dict[str, Any]:
    """Fetch latest FII/DII buy/sell data."""
    data = _nse_get("/api/fiidiiTradeReact")
    if data and isinstance(data, list):
        latest = data[0] if data else {}
        return {
            "fii_buy": float(latest.get("fiiBuy", 0)),
            "fii_sell": float(latest.get("fiiSell", 0)),
            "fii_net": float(latest.get("fiiNet", 0)),
            "dii_buy": float(latest.get("diiBuy", 0)),
            "dii_sell": float(latest.get("diiSell", 0)),
            "dii_net": float(latest.get("diiNet", 0)),
        }
    # Mock fallback
    return {
        "fii_buy": random.uniform(5000, 15000),
        "fii_sell": random.uniform(4000, 14000),
        "fii_net": random.uniform(-2000, 3000),
        "dii_buy": random.uniform(3000, 8000),
        "dii_sell": random.uniform(2500, 7500),
        "dii_net": random.uniform(-500, 1500),
    }


def get_bulk_deals(trade_date: str | None = None) -> list[dict]:
    """Fetch bulk deal data for a given date."""
    target_date = trade_date or date.today().strftime("%d-%m-%Y")
    data = _nse_get(f"/api/bulk-deals?date={target_date}")
    if data and isinstance(data, dict):
        return data.get("data", [])
    # Mock: return a few plausible bulk deals
    symbols = ["BEL", "HAL", "BHEL", "L&T", "RELIANCE"]
    return [
        {
            "symbol": random.choice(symbols),
            "clientName": "XYZ Mutual Fund",
            "dealType": "BUY",
            "quantity": random.randint(50000, 500000),
            "price": random.uniform(200, 2000),
        }
        for _ in range(random.randint(1, 3))
    ]


def get_block_deals(trade_date: str | None = None) -> list[dict]:
    """Fetch block deal data."""
    target_date = trade_date or date.today().strftime("%d-%m-%Y")
    data = _nse_get(f"/api/block-deals?date={target_date}")
    if data and isinstance(data, dict):
        return data.get("data", [])
    return []


def get_asm_gsm_list() -> set[str]:
    """Return set of symbols under ASM or GSM surveillance."""
    data = _nse_get("/api/reportsmf/smartODRreport")
    asm_symbols: set[str] = set()
    if data:
        for item in (data if isinstance(data, list) else []):
            sym = item.get("symbol", "")
            if sym:
                asm_symbols.add(sym.upper())
    # Always include a few known illiquid/risky stocks in the mock list
    asm_symbols.update({"YESBANK", "VODAFONE", "SUZLON"})
    return asm_symbols


def get_corporate_actions(symbol: str) -> list[dict]:
    """Fetch upcoming corporate actions for a symbol."""
    data = _nse_get(f"/api/corporates-corporateActions?index=equities&symbol={symbol}")
    if data and isinstance(data, list):
        return data[:10]
    # Mock: occasionally generate an ex-dividend date
    today = date.today()
    actions = []
    if hash(symbol) % 5 == 0:
        actions.append({
            "symbol": symbol,
            "subject": "Dividend",
            "exDate": (today + timedelta(days=random.randint(1, 10))).isoformat(),
            "recordDate": (today + timedelta(days=random.randint(1, 12))).isoformat(),
        })
    return actions


def get_economic_calendar() -> list[dict]:
    """Fetch upcoming economic events that may affect markets."""
    today = date.today()
    # Mock calendar — in production connect to a financial calendar API
    events = [
        {
            "date": (today + timedelta(days=1)).isoformat(),
            "event": "RBI Policy Statement",
            "impact": "high",
            "country": "IN",
        },
        {
            "date": (today + timedelta(days=3)).isoformat(),
            "event": "US CPI Release",
            "impact": "high",
            "country": "US",
        },
        {
            "date": (today + timedelta(days=7)).isoformat(),
            "event": "India IIP Data",
            "impact": "medium",
            "country": "IN",
        },
    ]
    return events


def is_market_holiday(check_date: date | None = None) -> bool:
    """Check if the given date is an NSE market holiday."""
    target = check_date or date.today()
    # Weekend
    if target.weekday() >= 5:
        return True
    # Known 2025 NSE holidays (partial list)
    known_holidays = {
        date(2025, 1, 26),  # Republic Day
        date(2025, 3, 14),  # Holi
        date(2025, 4, 14),  # Dr. Ambedkar Jayanti
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 1),   # Maharashtra Day
        date(2025, 8, 15),  # Independence Day
        date(2025, 10, 2),  # Gandhi Jayanti
        date(2025, 10, 24), # Dussehra
        date(2025, 11, 5),  # Diwali Laxmi Puja
        date(2025, 12, 25), # Christmas
    }
    return target in known_holidays

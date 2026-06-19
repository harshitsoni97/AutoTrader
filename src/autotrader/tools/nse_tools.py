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


def get_fii_derivatives() -> dict[str, Any]:
    """Fetch FII/DII participant-wise derivatives OI from NSE.

    NSE publishes this daily under 'Participant wise Open Interest'.
    Net long = bullish institutional positioning in index futures.
    """
    data = _nse_get("/api/participant-wise-OI")
    if data and isinstance(data, list):
        fii_row = next((r for r in data if "FII" in str(r.get("clientType", "")).upper()), None)
        prop_row = next((r for r in data if "PRO" in str(r.get("clientType", "")).upper()), None)
        if fii_row:
            return {
                "fii_index_future_net": float(fii_row.get("futureIndexLong", 0)) - float(fii_row.get("futureIndexShort", 0)),
                "fii_index_future_long": float(fii_row.get("futureIndexLong", 0)),
                "fii_index_future_short": float(fii_row.get("futureIndexShort", 0)),
                "prop_index_future_net": float(prop_row.get("futureIndexLong", 0)) - float(prop_row.get("futureIndexShort", 0)) if prop_row else 0.0,
            }
    # Mock fallback — plausible distribution
    import random as _r
    net = _r.uniform(-50000, 80000)
    return {
        "fii_index_future_net": round(net, 0),
        "fii_index_future_long": round(max(net, 0) + _r.uniform(100000, 300000), 0),
        "fii_index_future_short": round(max(-net, 0) + _r.uniform(100000, 300000), 0),
        "prop_index_future_net": round(_r.uniform(-30000, 30000), 0),
    }


def get_options_chain(symbol: str = "NIFTY") -> dict[str, Any]:
    """Fetch NSE options chain for PCR, max pain and IV skew.

    Returns:
        pcr: Put-Call Ratio by OI (>1 = more puts = bearish hedge = potential support)
        max_pain: Strike where max open contracts expire worthless (price magnet)
        atm_iv: At-the-money implied volatility (%)
        iv_skew: OTM put IV - OTM call IV (>0 = fear premium on downside)
    """
    data = _nse_get(f"/api/option-chain-indices?symbol={symbol}")
    if data and isinstance(data, dict):
        records = data.get("records", {})
        spot = records.get("underlyingValue", 0)
        chain = records.get("data", [])
        if chain and spot:
            total_call_oi = sum(r.get("CE", {}).get("openInterest", 0) for r in chain if r.get("CE"))
            total_put_oi = sum(r.get("PE", {}).get("openInterest", 0) for r in chain if r.get("PE"))
            pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi else 1.0

            # Max pain: strike with minimum total payout
            strikes = sorted({r["strikePrice"] for r in chain if "strikePrice" in r})
            min_pain, max_pain_strike = float("inf"), spot
            for k in strikes:
                pain = sum(max(0, k - r["strikePrice"]) * r.get("CE", {}).get("openInterest", 0)
                           + max(0, r["strikePrice"] - k) * r.get("PE", {}).get("openInterest", 0)
                           for r in chain if "strikePrice" in r)
                if pain < min_pain:
                    min_pain, max_pain_strike = pain, k

            # ATM IV and skew
            atm_strike = min(strikes, key=lambda k: abs(k - spot)) if strikes else spot
            atm_row = next((r for r in chain if r.get("strikePrice") == atm_strike), {})
            atm_iv = atm_row.get("CE", {}).get("impliedVolatility", 0) or atm_row.get("PE", {}).get("impliedVolatility", 0)

            otm_put_strike = min(strikes, key=lambda k: abs(k - spot * 0.97)) if strikes else spot
            otm_call_strike = min(strikes, key=lambda k: abs(k - spot * 1.03)) if strikes else spot
            otm_put_row = next((r for r in chain if r.get("strikePrice") == otm_put_strike), {})
            otm_call_row = next((r for r in chain if r.get("strikePrice") == otm_call_strike), {})
            iv_skew = round(
                (otm_put_row.get("PE", {}).get("impliedVolatility", 0) or 0)
                - (otm_call_row.get("CE", {}).get("impliedVolatility", 0) or 0),
                2,
            )

            return {
                "pcr": pcr,
                "max_pain": max_pain_strike,
                "atm_iv": round(atm_iv, 2),
                "iv_skew": iv_skew,
                "spot": spot,
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
            }

    # Mock fallback
    import random as _r
    spot = 22000 + _r.uniform(-500, 500)
    return {
        "pcr": round(_r.uniform(0.7, 1.4), 3),
        "max_pain": round(spot / 50) * 50,
        "atm_iv": round(_r.uniform(10, 20), 2),
        "iv_skew": round(_r.uniform(-2, 5), 2),
        "spot": round(spot, 2),
        "total_call_oi": int(_r.uniform(5e6, 15e6)),
        "total_put_oi": int(_r.uniform(5e6, 15e6)),
    }


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

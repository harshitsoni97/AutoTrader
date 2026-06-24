"""Universe building tools — index constituents, momentum screen, events."""
from __future__ import annotations

import structlog
import random
import time
from datetime import datetime, timedelta
from typing import Any

import requests

logger = structlog.get_logger()

NSE_BASE = "https://www.nseindia.com"
# niftyindices.com hosts the public constituent CSVs (nseindia.com path returns 404)
NIFTY_INDICES_BASE = "https://www.niftyindices.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.niftyindices.com/",
    "Connection": "keep-alive",
}

# Constituent CSVs live on niftyindices.com, not nseindia.com
INDEX_CSV_MAP = {
    "nifty50":  "/IndexConstituent/ind_nifty50list.csv",
    "nifty100": "/IndexConstituent/ind_nifty100list.csv",
    "nifty200": "/IndexConstituent/ind_nifty200list.csv",
    "nifty500": "/IndexConstituent/ind_nifty500list.csv",
}

# NSE symbol corrections — rename events or splits that break yfinance lookups.
# Applied to every symbol before it is passed to yfinance or NSE API calls.
SYMBOL_CORRECTIONS: dict[str, str] = {
    "ZOMATO": "ETERNAL",    # Zomato rebranded to Eternal Ltd on NSE

    "TATAMOTORS": "TMCV",  # Tata Motors demerged: TMCV (commercial), TMPV (passenger)
}

# Reverse map: what the CSV gives us → what yfinance/NSE API accepts
def normalize_symbol(symbol: str) -> str:
    """Return the canonical yfinance/NSE API symbol for a given NSE constituent name."""
    return SYMBOL_CORRECTIONS.get(symbol.upper(), symbol.upper())

# Fallback hardcoded set of 60 Nifty 500 symbols if CSV fetch fails
_FALLBACK_SYMBOLS = [
    {"symbol": "RELIANCE", "sector": "Energy"},
    {"symbol": "TCS", "sector": "IT"},
    {"symbol": "HDFCBANK", "sector": "Banking"},
    {"symbol": "ICICIBANK", "sector": "Banking"},
    {"symbol": "INFY", "sector": "IT"},
    {"symbol": "SBIN", "sector": "Banking"},
    {"symbol": "AXISBANK", "sector": "Banking"},
    {"symbol": "KOTAKBANK", "sector": "Banking"},
    {"symbol": "WIPRO", "sector": "IT"},
    {"symbol": "HCLTECH", "sector": "IT"},
    {"symbol": "TECHM", "sector": "IT"},
    {"symbol": "LT", "sector": "Capital_Goods"},
    {"symbol": "BEL", "sector": "Capital_Goods"},
    {"symbol": "HAL", "sector": "Capital_Goods"},
    {"symbol": "BHEL", "sector": "Capital_Goods"},
    {"symbol": "ABB", "sector": "Capital_Goods"},
    {"symbol": "SUNPHARMA", "sector": "Pharma"},
    {"symbol": "CIPLA", "sector": "Pharma"},
    {"symbol": "DRREDDY", "sector": "Pharma"},
    {"symbol": "DIVISLAB", "sector": "Pharma"},
    {"symbol": "AUROPHARMA", "sector": "Pharma"},
    {"symbol": "MARUTI", "sector": "Auto"},
    {"symbol": "TMCV", "sector": "Auto"},   # Tata Motors Commercial Vehicles
    {"symbol": "TMPV", "sector": "Auto"},   # Tata Motors Passenger Vehicles
    {"symbol": "M&M", "sector": "Auto"},
    {"symbol": "BAJAJ-AUTO", "sector": "Auto"},
    {"symbol": "HEROMOTOCO", "sector": "Auto"},
    {"symbol": "DLF", "sector": "Realty"},
    {"symbol": "GODREJPROP", "sector": "Realty"},
    {"symbol": "OBEROIRLTY", "sector": "Realty"},
    {"symbol": "TATASTEEL", "sector": "Metal"},
    {"symbol": "JSWSTEEL", "sector": "Metal"},
    {"symbol": "HINDALCO", "sector": "Metal"},
    {"symbol": "VEDL", "sector": "Metal"},
    {"symbol": "SAIL", "sector": "Metal"},
    {"symbol": "ONGC", "sector": "Energy"},
    {"symbol": "BPCL", "sector": "Energy"},
    {"symbol": "IOC", "sector": "Energy"},
    {"symbol": "POWERGRID", "sector": "Energy"},
    {"symbol": "HINDUNILVR", "sector": "FMCG"},
    {"symbol": "ITC", "sector": "FMCG"},
    {"symbol": "NESTLEIND", "sector": "FMCG"},
    {"symbol": "BRITANNIA", "sector": "FMCG"},
    {"symbol": "DABUR", "sector": "FMCG"},
    {"symbol": "POLYCAB", "sector": "Midcap"},
    {"symbol": "DELHIVERY", "sector": "Midcap"},
    {"symbol": "ETERNAL", "sector": "Midcap"},
    {"symbol": "NAUKRI", "sector": "Midcap"},
    {"symbol": "IRCTC", "sector": "Midcap"},
    {"symbol": "ASIANPAINT", "sector": "FMCG"},
    {"symbol": "BAJFINANCE", "sector": "Banking"},
    {"symbol": "BAJAJFINSV", "sector": "Banking"},
    {"symbol": "NTPC", "sector": "Energy"},
    {"symbol": "ADANIPORTS", "sector": "Capital_Goods"},
    {"symbol": "ADANIENT", "sector": "Energy"},
    {"symbol": "ULTRACEMCO", "sector": "Capital_Goods"},
    {"symbol": "GRASIM", "sector": "Capital_Goods"},
    {"symbol": "TITAN", "sector": "FMCG"},
    {"symbol": "INDUSINDBK", "sector": "Banking"},
    {"symbol": "COALINDIA", "sector": "Energy"},
    {"symbol": "HDFCLIFE", "sector": "Banking"},
]


def fetch_index_constituents(index: str = "nifty500", max_count: int = 100) -> list[dict]:
    """Fetch NSE index constituent list from niftyindices.com CSV.

    CSV format: Company Name,Industry,Symbol,Series,ISIN Code
    Falls back to hardcoded list if fetch fails.
    """
    path = INDEX_CSV_MAP.get(index.lower(), INDEX_CSV_MAP["nifty500"])
    try:
        resp = requests.get(
            f"{NIFTY_INDICES_BASE}{path}",
            headers={**HEADERS, "Accept": "text/csv,*/*"},
            timeout=15,
        )
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        result = []
        for line in lines[1:]:  # skip header
            parts = line.split(",")
            if len(parts) >= 3:
                raw_symbol = parts[2].strip().strip('"')
                industry = parts[1].strip().strip('"')
                symbol = normalize_symbol(raw_symbol)
                if symbol:
                    result.append({
                        "symbol": symbol,
                        "sector": _map_industry(industry),
                        "name": parts[0].strip().strip('"'),
                    })
        logger.info("Fetched %d constituents from %s", len(result), index)
        return result[:max_count]
    except Exception as e:
        logger.warning("NSE index CSV fetch failed for %s: %s — using fallback", index, e)
        return _FALLBACK_SYMBOLS[:max_count]


def _map_industry(industry: str) -> str:
    """Map NSE industry string to our sector names."""
    industry_lower = industry.lower()
    if any(k in industry_lower for k in ["bank", "finance", "insurance", "nbfc"]):
        return "Banking"
    if any(k in industry_lower for k in ["software", "it ", "information tech", "computer"]):
        return "IT"
    if any(k in industry_lower for k in ["pharma", "drug", "biotech", "hospital", "health"]):
        return "Pharma"
    if any(k in industry_lower for k in ["auto", "vehicle", "tyre"]):
        return "Auto"
    if any(k in industry_lower for k in ["real estate", "realty", "housing", "property"]):
        return "Realty"
    if any(k in industry_lower for k in ["steel", "metal", "alumin", "copper", "zinc", "mining"]):
        return "Metal"
    if any(k in industry_lower for k in ["oil", "gas", "petro", "refin", "power", "energy", "electricity"]):
        return "Energy"
    if any(k in industry_lower for k in ["fmcg", "consumer", "food", "beverag", "tobacco", "personal care"]):
        return "FMCG"
    if any(k in industry_lower for k in ["capital goods", "infra", "construct", "engineer", "defence", "cement"]):
        return "Capital_Goods"
    return "Midcap"


def _momentum_score_from_rows(rows: list[dict]) -> float:
    """Compute momentum score 0-100 from a list of OHLCV row dicts."""
    if not rows or len(rows) < 5:
        return 40.0
    closes = [r["close"] for r in rows]
    volumes = [r.get("volume", 0) for r in rows]
    latest = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma5
    vol_5d = sum(volumes[-5:]) / 5 if volumes else 1
    vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else vol_5d
    vol_surge = vol_5d / vol_20d if vol_20d > 0 else 1.0
    price_vs_ma5 = (latest / ma5 - 1) * 100 if ma5 > 0 else 0
    price_vs_ma20 = (latest / ma20 - 1) * 100 if ma20 > 0 else 0
    score = 50.0
    score += min(20, price_vs_ma5 * 4)
    score += min(15, price_vs_ma20 * 2)
    score += min(15, (vol_surge - 1) * 30)
    return max(0.0, min(100.0, score))


def momentum_screen(symbols: list[dict], top_n: int = 50) -> list[dict]:
    """Score each symbol by momentum (price vs MAs, volume surge).

    Uses Upstox daily candles as primary source (works on cloud servers).
    Falls back to yfinance if Upstox unavailable.
    Returns top_n by score with source='momentum'.
    """
    import json, os
    from datetime import date, timedelta

    # Load instrument map for Upstox lookups
    map_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../../config/upstox_instruments.json"))
    instrument_map: dict = {}
    try:
        with open(map_path) as f:
            instrument_map = json.load(f)
    except Exception:
        pass

    scored = []

    for s in symbols:
        sym = normalize_symbol(s["symbol"])
        rows = []

        # Primary: Upstox daily candles
        ikey = instrument_map.get(sym)
        if ikey:
            try:
                from autotrader.tools import upstox_data
                today_str = date.today().strftime("%Y-%m-%d")
                from_str = (date.today() - timedelta(days=40)).strftime("%Y-%m-%d")
                rows = upstox_data.get_historical_candles(ikey, "days", 1, from_str, today_str) or []
                if rows:
                    rows.sort(key=lambda r: r.get("timestamp", ""))
            except Exception as exc:
                logger.debug("Upstox momentum fetch failed for %s: %s", sym, exc)

        # Fallback: yfinance (may be blocked on cloud IPs)
        if not rows or len(rows) < 5:
            try:
                import yfinance as yf
                df = yf.download(f"{sym}.NS", period="30d", interval="1d", progress=False, auto_adjust=True)
                if df is not None and not df.empty and len(df) >= 5:
                    rows = [
                        {"close": float(row["Close"]), "volume": float(row["Volume"]),
                         "open": float(row["Open"]), "high": float(row["High"]), "low": float(row["Low"])}
                        for _, row in df.iterrows()
                    ]
            except Exception:
                pass

        score = _momentum_score_from_rows(rows)
        source = "momentum" if rows else "index"
        scored.append({**s, "source": source, "momentum_score": round(score, 1)})

    scored.sort(key=lambda x: x.get("momentum_score", 0), reverse=True)
    return scored[:top_n]


def get_event_driven_symbols(corporate_events: list[dict]) -> list[dict]:
    """Extract symbols with upcoming results/board meetings from corporate events.
    
    Returns list of {symbol, sector, source='event', event_type, event_date}.
    """
    result = []
    today = datetime.today().date()
    week_out = today + timedelta(days=7)
    for event in corporate_events:
        ex_date_str = event.get("exDate", "") or event.get("date", "")
        subject = (event.get("subject", "") or "").lower()
        symbol = (event.get("symbol", "") or "").upper()
        if not symbol:
            continue
        try:
            ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if today <= ex_date <= week_out:
            event_type = "results" if "result" in subject or "board" in subject else "dividend" if "dividend" in subject or "buyback" in subject else "corporate_event"
            result.append({
                "symbol": symbol,
                "sector": "Unknown",
                "source": "event",
                "event_type": event_type,
                "event_date": ex_date_str,
            })
    return result


def get_preopen_movers(top_n: int = 20) -> list[dict]:
    """Fetch NSE pre-open session data (9:00-9:08 IST only).
    
    Returns top_n symbols by price change in pre-open.
    Only meaningful during 9:00-9:08 IST; returns empty list outside that window.
    """
    now = datetime.utcnow()
    # IST = UTC+5:30; pre-open window: 3:30-3:38 UTC
    ist_hour = (now.hour + 5) % 24 + (1 if now.minute >= 30 else 0)
    ist_minute = (now.minute + 30) % 60
    if not (ist_hour == 9 and ist_minute < 8):
        logger.debug("Pre-open data only available 9:00-9:08 IST, skipping")
        return []

    try:
        session = requests.Session()
        session.get(NSE_BASE, headers=HEADERS, timeout=15)
        time.sleep(0.3)
        resp = session.get(f"{NSE_BASE}/api/market-data-pre-open?key=NIFTY", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        movers = []
        for r in records:
            meta = r.get("metadata", {})
            symbol = meta.get("symbol", "")
            change_pct = meta.get("pChange", 0)
            if symbol and abs(change_pct) > 0.5:
                movers.append({
                    "symbol": symbol,
                    "sector": "Unknown",
                    "source": "preopen",
                    "preopen_change_pct": change_pct,
                })
        movers.sort(key=lambda x: abs(x.get("preopen_change_pct", 0)), reverse=True)
        return movers[:top_n]
    except Exception as e:
        logger.warning("Pre-open data fetch failed: %s", e)
        return []

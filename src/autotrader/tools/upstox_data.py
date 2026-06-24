"""Upstox Analytics API client — read-only, uses 1-year Analytics Token.

All functions degrade gracefully: return None (or empty collections) on
failure so callers can fall back to yfinance / NSE / mock data.

Token source: env var UPSTOX_ANALYTICS_TOKEN
Base URL: https://api.upstox.com
"""

from __future__ import annotations

import structlog
import os
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import requests

logger = structlog.get_logger()

BASE_URL = "https://api.upstox.com"
_TIMEOUT = 10
_RETRY_DELAY = 1.0  # seconds between first attempt and single retry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _token() -> str | None:
    tok = os.getenv("UPSTOX_ANALYTICS_TOKEN")
    if not tok:
        logger.warning("UPSTOX_ANALYTICS_TOKEN not set — Upstox calls will be skipped")
    return tok


def _headers(tok: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/json",
    }


def _get(url: str, params: dict | None = None) -> dict | list | None:
    """Single GET helper with one retry.  Never raises; returns None on failure."""
    tok = _token()
    if not tok:
        return None

    hdrs = _headers(tok)
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=hdrs, params=params, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Upstox %s returned HTTP %s (attempt %d): %s",
                url, resp.status_code, attempt + 1, resp.text[:200],
            )
        except requests.RequestException as exc:
            logger.warning("Upstox request error %s (attempt %d): %s", url, attempt + 1, exc)

        if attempt == 0:
            time.sleep(_RETRY_DELAY)

    return None


# ---------------------------------------------------------------------------
# 1. LTP (Last Traded Price)
# ---------------------------------------------------------------------------

def get_ltp(instrument_keys: list[str]) -> dict[str, float] | None:
    """Fetch last traded prices for a list of instrument keys.

    Returns {instrument_key: last_price} dict or None on failure.
    """
    if not instrument_keys:
        return {}

    keys_param = ",".join(instrument_keys)
    url = f"{BASE_URL}/v3/market-quote/ltp"
    data = _get(url, params={"instrument_key": keys_param})
    if data is None:
        return None

    # Response shape: {"status": "success", "data": {"NSE_INDEX|Nifty 50": {"last_price": 22000.0, ...}, ...}}
    raw = data.get("data", {}) if isinstance(data, dict) else {}
    result: dict[str, float] = {}
    for key, val in raw.items():
        if isinstance(val, dict) and "last_price" in val:
            # Upstox returns keys with colon separator in response (NSE_EQ:SYMBOL)
            # but callers pass pipe separator (NSE_EQ|SYMBOL) — normalise both
            normalised = key.replace(":", "|")
            result[normalised] = float(val["last_price"])
    return result if result else None


# ---------------------------------------------------------------------------
# 2. VIX
# ---------------------------------------------------------------------------

def get_vix() -> dict[str, float] | None:
    """Return {"vix": float, "vix_prev": float} or None on failure."""
    vix_key = "NSE_INDEX|India VIX"
    tok = _token()
    if not tok:
        return None

    url = f"{BASE_URL}/v3/market-quote/ltp"
    data = _get(url, params={"instrument_key": vix_key})
    if data is None:
        return None

    raw = data.get("data", {}) if isinstance(data, dict) else {}
    # Response keys use colon separator; normalise to pipe for lookup
    raw = {k.replace(":", "|"): v for k, v in raw.items()}
    entry = raw.get(vix_key, {})
    if not entry:
        logger.warning("Upstox VIX: no data for %s", vix_key)
        return None

    vix_val = entry.get("last_price")
    vix_prev = entry.get("cp")  # previous close

    if vix_val is None:
        return None

    return {
        "vix": float(vix_val),
        "vix_prev": float(vix_prev) if vix_prev is not None else float(vix_val),
    }


# ---------------------------------------------------------------------------
# 3. Historical candles
# ---------------------------------------------------------------------------

def get_historical_candles(
    instrument_key: str,
    unit: str,
    interval: int,
    from_date: str,
    to_date: str,
) -> list[dict] | None:
    """Fetch OHLCV candles from Upstox v3 historical-candle endpoint.

    Args:
        instrument_key: e.g. "NSE_INDEX|Nifty 50"
        unit:           e.g. "days", "minutes", "hours"
        interval:       candle size in units
        from_date:      YYYY-MM-DD
        to_date:        YYYY-MM-DD

    Returns list of dicts with keys: timestamp, open, high, low, close, volume
    or None on failure.
    """
    encoded_key = quote(instrument_key, safe="")
    url = f"{BASE_URL}/v3/historical-candle/{encoded_key}/{unit}/{interval}/{to_date}/{from_date}"
    data = _get(url)
    if data is None:
        return None

    # Response: {"status": "success", "data": {"candles": [[ts, o, h, l, c, v, oi], ...]}}
    raw = data.get("data", {}) if isinstance(data, dict) else {}
    candles = raw.get("candles", [])
    if not isinstance(candles, list):
        return None

    result = []
    for c in candles:
        # [timestamp, open, high, low, close, volume, oi]
        if len(c) < 6:
            continue
        result.append({
            "timestamp": str(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": int(c[5]),
        })
    return result if result else None


# ---------------------------------------------------------------------------
# 4. Nifty daily data
# ---------------------------------------------------------------------------

def get_nifty_data(days: int = 25) -> list[dict] | None:
    """Fetch Nifty 50 daily OHLCV candles for the last `days` trading days.

    Returns same dict format as market_data.get_nifty_data() or None on failure.
    """
    today = date.today()
    from_date = (today - timedelta(days=days + 10)).strftime("%Y-%m-%d")  # buffer for weekends/holidays
    to_date = today.strftime("%Y-%m-%d")

    candles = get_historical_candles("NSE_INDEX|Nifty 50", "days", 1, from_date, to_date)
    if candles is None:
        return None

    # Sort ascending (API may return newest-first)
    candles.sort(key=lambda x: x["timestamp"])
    # Keep last `days` records
    return candles[-days:] if len(candles) > days else candles


# ---------------------------------------------------------------------------
# 5. Options chain
# ---------------------------------------------------------------------------

def _get_nearest_expiry(symbol: str) -> str | None:
    """Find nearest options expiry >= today for the given symbol."""
    instrument_key = f"NSE_INDEX|{symbol}"
    url = f"{BASE_URL}/v2/option/contract"
    data = _get(url, params={"instrument_key": instrument_key})
    if data is None:
        return None

    contracts = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    today_str = date.today().isoformat()

    # Collect all expiry dates >= today
    future_expiries = []
    for c in contracts:
        expiry = c.get("expiry", "")
        if expiry and expiry >= today_str:
            future_expiries.append(expiry)

    if not future_expiries:
        return None

    return min(future_expiries)


def get_max_pain(instrument_key: str, expiry: str) -> float | None:
    """Fetch max pain strike from Upstox API."""
    today_str = date.today().isoformat()
    url = f"{BASE_URL}/v2/market/max-pain"
    data = _get(url, params={
        "instrument_key": instrument_key,
        "expiry": expiry,
        "date": today_str,
        "bucket_interval": 60,
    })
    if data is None:
        return None

    inner = data.get("data", {}) if isinstance(data, dict) else {}
    max_pain_val = inner.get("max_pain")
    return float(max_pain_val) if max_pain_val is not None else None


def get_pcr(expiry: str) -> float | None:
    """Fetch overall PCR for Nifty 50 options for a given expiry."""
    today_str = date.today().isoformat()
    url = f"{BASE_URL}/v2/market/pcr"
    data = _get(url, params={
        "instrument_key": "NSE_INDEX|Nifty 50",
        "expiry": expiry,
        "date": today_str,
        "bucket_interval": 60,
    })
    if data is None:
        return None

    inner = data.get("data", {}) if isinstance(data, dict) else {}
    pcr_val = inner.get("pcr")
    return float(pcr_val) if pcr_val is not None else None


def get_options_chain(symbol: str = "Nifty 50") -> dict[str, Any] | None:
    """Fetch options chain and compute PCR, max pain, ATM IV, IV skew.

    Returns same dict format as nse_tools.get_options_chain() or None on failure.
    """
    instrument_key = f"NSE_INDEX|{symbol}"

    # Step 1: get nearest expiry
    nearest_expiry = _get_nearest_expiry(symbol)
    if not nearest_expiry:
        logger.warning("Upstox options chain: could not determine nearest expiry for %s", symbol)
        return None

    # Step 2: fetch option chain data
    url = f"{BASE_URL}/v2/option/chain"
    data = _get(url, params={
        "instrument_key": instrument_key,
        "expiry_date": nearest_expiry,
    })
    if data is None:
        return None

    strikes_data = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not strikes_data:
        logger.warning("Upstox options chain: empty chain for %s expiry %s", symbol, nearest_expiry)
        return None

    # Compute aggregate metrics
    total_call_oi = 0
    total_put_oi = 0

    strike_records = []
    for row in strikes_data:
        ce = row.get("call_options", {}) or {}
        pe = row.get("put_options", {}) or {}
        ce_md = ce.get("market_data", {}) or {}
        pe_md = pe.get("market_data", {}) or {}
        ce_greeks = ce.get("option_greeks", {}) or {}
        pe_greeks = pe.get("option_greeks", {}) or {}

        strike_price = float(row.get("strike_price", 0))
        ce_oi = int(ce_md.get("oi", 0) or 0)
        pe_oi = int(pe_md.get("oi", 0) or 0)
        ce_iv = float(ce_greeks.get("iv", 0) or 0)
        pe_iv = float(pe_greeks.get("iv", 0) or 0)
        ce_ltp = float(ce_md.get("ltp", 0) or 0)

        total_call_oi += ce_oi
        total_put_oi += pe_oi

        strike_records.append({
            "strike": strike_price,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "ce_iv": ce_iv,
            "pe_iv": pe_iv,
            "ce_ltp": ce_ltp,
            "pcr": float(row.get("pcr", 0) or 0),
        })

    if not strike_records:
        return None

    # Determine spot: use the row-level PCR-weighted or pick from LTP data
    # Upstox chain rows have call/put LTP — spot can be inferred from ATM
    # Alternatively fetch via get_ltp
    ltp_data = get_ltp([instrument_key])
    spot = float(ltp_data.get(instrument_key, 0)) if ltp_data else 0.0
    if spot == 0.0:
        logger.warning("Upstox options chain: could not fetch spot price for %s", symbol)

    # Overall PCR
    pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 1.0

    # ATM strike (closest to spot)
    if spot > 0:
        atm_record = min(strike_records, key=lambda r: abs(r["strike"] - spot))
    else:
        # No spot — use strike with smallest |ce_ltp - put_ltp| as proxy
        atm_record = strike_records[len(strike_records) // 2]

    atm_iv = atm_record["ce_iv"] or atm_record["pe_iv"]

    # IV skew: OTM put (spot * 0.97) vs OTM call (spot * 1.03)
    iv_skew = 0.0
    if spot > 0:
        otm_put_target = spot * 0.97
        otm_call_target = spot * 1.03
        otm_put_rec = min(strike_records, key=lambda r: abs(r["strike"] - otm_put_target))
        otm_call_rec = min(strike_records, key=lambda r: abs(r["strike"] - otm_call_target))
        iv_skew = round((otm_put_rec["pe_iv"] or 0) - (otm_call_rec["ce_iv"] or 0), 2)

    # Max pain
    max_pain_val = get_max_pain(instrument_key, nearest_expiry)
    if max_pain_val is None:
        # Compute locally
        strikes = [r["strike"] for r in strike_records]
        min_pain = float("inf")
        max_pain_val = spot
        for k in strikes:
            pain = sum(
                max(0.0, k - r["strike"]) * r["ce_oi"]
                + max(0.0, r["strike"] - k) * r["pe_oi"]
                for r in strike_records
            )
            if pain < min_pain:
                min_pain = pain
                max_pain_val = k

    return {
        "pcr": pcr,
        "max_pain": float(max_pain_val),
        "atm_iv": round(float(atm_iv), 2),
        "iv_skew": iv_skew,
        "spot": spot,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "source": "upstox",
    }


# ---------------------------------------------------------------------------
# 7. FII data
# ---------------------------------------------------------------------------

def get_fii_data() -> dict[str, Any] | None:
    """Fetch FII derivatives data from Upstox.

    Returns dict with fii_net, fii_future_net, fii_call_long, fii_put_long
    or None on failure.
    """
    url = f"{BASE_URL}/v2/market/fii"
    data = _get(url, params=[
        ("data_type", "NSE_FO|INDEX_FUTURES"),
        ("data_type", "NSE_FO|INDEX_OPTIONS"),
        ("interval", "1D"),
    ])
    if data is None:
        return None

    segments = data.get("data", {}) if isinstance(data, dict) else {}
    if not isinstance(segments, dict):
        # Some responses may return as list — handle list-of-segment-dicts
        if isinstance(segments, list):
            seg_dict: dict[str, Any] = {}
            for seg in segments:
                seg_type = seg.get("data_type") or seg.get("segment", "")
                seg_dict[seg_type] = seg.get("data", [seg])
            segments = seg_dict

    futures_records = segments.get("NSE_FO|INDEX_FUTURES", [])
    options_records = segments.get("NSE_FO|INDEX_OPTIONS", [])

    futures_latest = futures_records[0] if futures_records else {}
    options_latest = options_records[0] if options_records else {}

    # From INDEX_FUTURES
    fii_future_long = int(futures_latest.get("total_long_contracts", 0) or 0)
    fii_future_short = int(futures_latest.get("total_short_contracts", 0) or 0)
    fii_future_net = fii_future_long - fii_future_short

    buy_amount = float(futures_latest.get("buy_amount", 0) or 0)
    sell_amount = float(futures_latest.get("sell_amount", 0) or 0)
    fii_net = buy_amount - sell_amount

    # From INDEX_OPTIONS
    fii_call_long = int(options_latest.get("total_call_long_contracts", 0) or 0)
    fii_put_long = int(options_latest.get("total_put_long_contracts", 0) or 0)

    if not futures_latest and not options_latest:
        logger.warning("Upstox FII data: no records returned")
        return None

    return {
        "fii_net": round(fii_net, 2),
        "fii_future_net": fii_future_net,
        "fii_call_long": fii_call_long,
        "fii_put_long": fii_put_long,
        "source": "upstox",
    }


# ---------------------------------------------------------------------------
# 8. Market status
# ---------------------------------------------------------------------------

def get_market_status() -> bool | None:
    """Return True if NSE is NORMAL_OPEN, False otherwise, None on failure."""
    url = f"{BASE_URL}/v2/market/status/NSE"
    data = _get(url)
    if data is None:
        return None

    inner = data.get("data", {}) if isinstance(data, dict) else {}
    status = inner.get("status", "")
    return status == "NORMAL_OPEN"


# ---------------------------------------------------------------------------
# 9. Market holidays
# ---------------------------------------------------------------------------

def get_market_holidays() -> list[str] | None:
    """Return list of NSE holiday date strings (YYYY-MM-DD) or None on failure."""
    url = f"{BASE_URL}/v2/market/holidays"
    data = _get(url)
    if data is None:
        return None

    holidays_raw = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    result = []
    for h in holidays_raw:
        closed = h.get("closed_exchanges", [])
        if "NSE" in closed:
            holiday_date = h.get("date", "")
            if holiday_date:
                result.append(str(holiday_date)[:10])  # ensure YYYY-MM-DD
    return result

"""Live prices for the long-term advisor from the Upstox API (read-only).

Uses UPSTOX_ANALYTICS_TOKEN (Upstox's long-lived, read-only analytics token)
or UPSTOX_ACCESS_TOKEN. Holdings are keyed by ISIN, which Upstox accepts
directly as NSE_EQ|<ISIN>; anything without an ISIN (e.g. a core ETF not yet
owned) is resolved through Upstox's public NSE instrument master.

Writes investor/prices.json:
  {"as_of": "...", "source": "upstox",
   "prices": {"TMPV": 266.1, ...},
   "stats": {"TMPV": {"high_52w": .., "low_52w": .., "return_1y_pct": ..}}}
"""
from __future__ import annotations

import datetime
import gzip
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
INDEX_KEYS = {"NIFTY 50": "NSE_INDEX|Nifty 50", "NIFTY NEXT 50": "NSE_INDEX|Nifty Next 50",
              "NIFTY MIDCAP 150": "NSE_INDEX|NIFTY MIDCAP 150", "INDIA VIX": "NSE_INDEX|India VIX"}


def _token() -> str:
    tok = os.getenv("UPSTOX_ANALYTICS_TOKEN") or os.getenv("UPSTOX_ACCESS_TOKEN")
    if not tok:
        raise RuntimeError("set UPSTOX_ANALYTICS_TOKEN (or UPSTOX_ACCESS_TOKEN)")
    return tok


def _get(url: str, timeout: float = 20) -> dict:
    req = Request(url, headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json",
                                "User-Agent": "autotrader-longterm/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def instrument_master(cache: Path) -> dict[str, str]:
    """NSE trading symbol -> instrument_key, cached for a day."""
    if not cache.exists() or (datetime.datetime.now().timestamp() - cache.stat().st_mtime) > 86400:
        with urlopen(INSTRUMENTS_URL, timeout=60) as r:
            rows = json.loads(gzip.decompress(r.read()))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({x["trading_symbol"]: x["instrument_key"] for x in rows
                                     if x.get("segment") == "NSE_EQ"}))
    return json.loads(cache.read_text())


def resolve_keys(symbols: dict[str, str], master: dict[str, str] | None = None) -> dict[str, str]:
    """symbols: {SYMBOL: ISIN or ""} -> {SYMBOL: instrument_key}. Unresolvable ones are dropped."""
    out = {}
    for sym, isin in symbols.items():
        if "|" in isin:  # an explicit instrument key
            out[sym] = isin
        elif sym.upper() in INDEX_KEYS:
            out[sym] = INDEX_KEYS[sym.upper()]
        elif isin:
            out[sym] = f"NSE_EQ|{isin}"
        elif master and sym in master:
            out[sym] = master[sym]
    return out


def parse_ltp(payload: dict, keys: dict[str, str]) -> dict[str, float]:
    """Upstox keys the response as 'NSE_EQ:SYMBOL' and echoes instrument_token; match on the token."""
    by_key = {v: k for k, v in keys.items()}
    out = {}
    for item in (payload.get("data") or {}).values():
        sym = by_key.get(item.get("instrument_token"))
        if sym and item.get("last_price") is not None:
            out[sym] = float(item["last_price"])
    return out


def fetch_ltp(keys: dict[str, str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    items = list(keys.items())
    for i in range(0, len(items), 500):  # API limit per call
        chunk = dict(items[i:i + 500])
        q = ",".join(quote(k, safe="") for k in chunk.values())
        prices.update(parse_ltp(_get(f"{API}/v2/market-quote/ltp?instrument_key={q}"), chunk))
    return prices


def candle_stats(candles: list[list]) -> dict:
    """Daily candles [ts, open, high, low, close, volume, oi], newest first (Upstox order)."""
    if not candles:
        return {}
    closes = [c[4] for c in candles]
    return {"high_52w": max(c[2] for c in candles), "low_52w": min(c[3] for c in candles),
            "return_1y_pct": round((closes[0] / closes[-1] - 1) * 100, 1),
            "off_high_pct": round((closes[0] / max(c[2] for c in candles) - 1) * 100, 1)}


def fetch_year_stats(key: str, today: datetime.date | None = None) -> dict:
    today = today or datetime.date.today()
    start = today - datetime.timedelta(days=365)
    url = f"{API}/v3/historical-candle/{quote(key, safe='')}/days/1/{today:%Y-%m-%d}/{start:%Y-%m-%d}"
    return candle_stats((_get(url).get("data") or {}).get("candles") or [])


def refresh(symbols: dict[str, str], out: Path, with_stats: bool = True,
            master_cache: Path | None = None) -> dict:
    master = None
    if any(not isin and s.upper() not in INDEX_KEYS for s, isin in symbols.items()):
        try:
            master = instrument_master(master_cache or out.parent / "upstox_nse_master.json")
        except OSError:
            master = None  # symbols without an ISIN are reported as unresolved
    keys = resolve_keys(symbols, master)
    prices = fetch_ltp(keys)
    # Some ISINs trade only on BSE (e.g. NSE's own shares): retry those there.
    retry = {s: k.replace("NSE_EQ|", "BSE_EQ|") for s, k in keys.items()
             if s not in prices and k.startswith("NSE_EQ|")}
    if retry:
        bse = fetch_ltp(retry)
        prices.update(bse)
        keys.update({s: retry[s] for s in bse})
    stats, failed = {}, []
    for sym, key in (keys.items() if with_stats else ()):
        try:
            stats[sym] = fetch_year_stats(key)
        except OSError:
            failed.append(sym)
    result = {"as_of": datetime.datetime.now().isoformat(timespec="seconds"), "source": "upstox",
              "prices": prices, "stats": stats, "stats_failed": failed,
              "unresolved": sorted(set(symbols) - set(prices))}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    return result

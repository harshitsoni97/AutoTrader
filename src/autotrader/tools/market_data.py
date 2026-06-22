"""Market data fetching tools — uses yfinance with graceful fallbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _yf_download(ticker: str, period: str = "5d", interval: str = "1d") -> list[dict]:
    """Download OHLCV data via yfinance, return list of row dicts."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            return []
        df = df.reset_index()
        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row.get("Date", row.get("Datetime", ""))),
                "open": float(row["Open"].iloc[0]) if hasattr(row["Open"], "iloc") else float(row["Open"]),
                "high": float(row["High"].iloc[0]) if hasattr(row["High"], "iloc") else float(row["High"]),
                "low": float(row["Low"].iloc[0]) if hasattr(row["Low"], "iloc") else float(row["Low"]),
                "close": float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"]),
                "volume": float(row["Volume"].iloc[0]) if hasattr(row["Volume"], "iloc") else float(row["Volume"]),
            })
        return records
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", ticker, e)
        return []


def _mock_ohlcv(ticker: str, base_price: float = 100.0, days: int = 25) -> list[dict]:
    """Generate mock OHLCV data when real data unavailable."""
    import random
    random.seed(hash(ticker) % 10000)
    records = []
    price = base_price
    for i in range(days):
        pct = random.uniform(-0.02, 0.025)
        close = round(price * (1 + pct), 2)
        records.append({
            "date": (datetime.today() - timedelta(days=days - i)).strftime("%Y-%m-%d"),
            "open": round(price, 2),
            "high": round(max(price, close) * random.uniform(1.001, 1.015), 2),
            "low": round(min(price, close) * random.uniform(0.985, 0.999), 2),
            "close": close,
            "volume": random.randint(500_000, 5_000_000),
        })
        price = close
    return records


def get_nifty_data(period: str = "25d") -> list[dict]:
    data = _yf_download("^NSEI", period=period)
    return data if data else _mock_ohlcv("NIFTY", 22000)


def get_banknifty_data(period: str = "25d") -> list[dict]:
    data = _yf_download("^NSEBANK", period=period)
    return data if data else _mock_ohlcv("BANKNIFTY", 47000)


def get_vix_data(period: str = "5d") -> dict[str, Any]:
    rows = _yf_download("^INDIAVIX", period=period)
    if rows:
        latest = rows[-1]
        return {"vix": latest["close"], "vix_prev": rows[-2]["close"] if len(rows) > 1 else latest["close"]}
    return {"vix": 13.5, "vix_prev": 13.0}


def get_gift_nifty() -> dict[str, Any]:
    """GIFT Nifty proxy — yfinance has no direct GIFT Nifty feed.

    Fall back to Nifty spot with a small synthetic premium, which is
    close enough for gap detection purposes. Replace with a real GIFT
    Nifty data source (broker API or investing.com scrape) when available.
    """
    nifty = get_nifty_data(period="2d")
    base = nifty[-1]["close"] if nifty else 22000
    return {"gift_nifty": round(base * 1.001, 2), "change_pct": 0.1}


def get_usdinr() -> dict[str, Any]:
    rows = _yf_download("USDINR=X", period="5d")
    if rows:
        return {"usdinr": rows[-1]["close"], "change_pct": round((rows[-1]["close"] / rows[-2]["close"] - 1) * 100, 4) if len(rows) > 1 else 0.0}
    return {"usdinr": 83.5, "change_pct": 0.0}


def get_crude_oil() -> dict[str, Any]:
    rows = _yf_download("CL=F", period="5d")
    if rows:
        return {"crude_usd": rows[-1]["close"]}
    return {"crude_usd": 78.0}


def get_us_markets() -> dict[str, Any]:
    spx = _yf_download("^GSPC", period="2d")
    ndq = _yf_download("^IXIC", period="2d")

    def pct(rows):
        if len(rows) >= 2:
            return round((rows[-1]["close"] / rows[-2]["close"] - 1) * 100, 4)
        return 0.0

    return {
        "sp500_change_pct": pct(spx) if spx else 0.2,
        "nasdaq_change_pct": pct(ndq) if ndq else 0.3,
    }


def get_global_markets() -> dict[str, Any]:
    result = {}
    result.update(get_us_markets())
    result.update(get_usdinr())
    result.update(get_crude_oil())
    result.update(get_gift_nifty())
    return result


def get_stock_data(symbol: str, period: str = "25d") -> list[dict]:
    """Fetch NSE stock OHLCV. Tries yfinance first, then Upstox historical candles."""
    ns_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    data = _yf_download(ns_symbol, period=period)
    if data:
        return data

    # Upstox fallback using instrument map
    try:
        from autotrader.tools import upstox_data
        import json, os
        from datetime import date, timedelta
        map_path = os.path.join(os.path.dirname(__file__), "../../../config/upstox_instruments.json")
        map_path = os.path.normpath(map_path)
        with open(map_path) as f:
            instrument_map: dict = json.load(f)
        ikey = instrument_map.get(symbol)
        if ikey:
            days = int(period.replace("d", ""))
            from_date = (date.today() - timedelta(days=days + 10)).strftime("%Y-%m-%d")
            to_date = date.today().strftime("%Y-%m-%d")
            rows = upstox_data.get_historical_candles(ikey, "days", 1, from_date, to_date)
            if rows:
                logger.info("get_stock_data: using Upstox for %s", symbol)
                return rows
    except Exception as exc:
        logger.debug("Upstox stock fallback failed for %s: %s", symbol, exc)

    base = {"BEL": 412, "RELIANCE": 2800, "INFY": 1750, "TCS": 4100}.get(symbol, 200)
    return _mock_ohlcv(symbol, base)


def get_sector_etf_data() -> dict[str, list[dict]]:
    """Fetch sector ETF performance for rotation analysis."""
    sector_map = {
        "Banking": "^NSEBANK",
        "IT": "^CNXIT",
        "Pharma": "^CNXPHARMA",
        "Auto": "^CNXAUTO",
        "FMCG": "^CNXFMCG",
        "Realty": "^CNXREALTY",
        "Metal": "^CNXMETAL",
        "Energy": "^CNXENERGY",
        "Capital_Goods": "^CNXINFRA",
        "Midcap": "^NSEMDCP50",
    }
    result: dict[str, list[dict]] = {}
    for sector, ticker in sector_map.items():
        rows = _yf_download(ticker, period="10d")
        result[sector] = rows if rows else _mock_ohlcv(sector, 15000, 10)
    return result

#!/usr/bin/env python3
"""Fetch 30-minute intraday candles for given symbols on a given date via Upstox.

Usage:
    python3 scripts/fetch_intraday.py 2026-06-25 DRREDDY AJANTPHARM CIPLA

Requires UPSTOX_ANALYTICS_TOKEN in the environment (loaded from .env).
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Load .env so UPSTOX_ANALYTICS_TOKEN is available
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env, override=True)
    except ImportError:
        for line in _env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ[k.strip()] = v.strip()

from autotrader.tools import upstox_data
from autotrader.agents.layer2.technical_structure import _load_instrument_map


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/fetch_intraday.py YYYY-MM-DD SYMBOL [SYMBOL ...]")
        sys.exit(1)

    target_date = sys.argv[1]
    symbols = sys.argv[2:]
    imap = _load_instrument_map()

    for sym in symbols:
        ikey = imap.get(sym)
        if not ikey:
            print(f"\n{sym}: NOT in instrument map (run scripts/update_instruments.py)")
            continue

        # Pull a 2-day window so the requested day is fully covered, then filter.
        from datetime import date, timedelta
        d = date.fromisoformat(target_date)
        frm = (d - timedelta(days=1)).isoformat()
        to = (d + timedelta(days=1)).isoformat()

        rows = upstox_data.get_historical_candles(ikey, "minutes", 30, frm, to)
        if not rows:
            print(f"\n{sym}: no data returned (market closed that day, or token unset)")
            continue

        rows = [r for r in rows if str(r.get("timestamp", "")).startswith(target_date)]
        rows.sort(key=lambda r: r["timestamp"])
        if not rows:
            print(f"\n{sym}: no candles on {target_date} (likely a holiday/weekend)")
            continue

        open_p = rows[0]["open"]
        close_p = rows[-1]["close"]
        day_high = max(r["high"] for r in rows)
        day_low = min(r["low"] for r in rows)
        move = (close_p / open_p - 1) * 100

        print(f"\n=== {sym}  {target_date} ===")
        print(f"Open {open_p:.2f} | High {day_high:.2f} | Low {day_low:.2f} | "
              f"Close {close_p:.2f} | Day move {move:+.2f}%")
        print(f"{'Time':<6} {'Open':>9} {'High':>9} {'Low':>9} {'Close':>9} {'Volume':>12}")
        for r in rows:
            ts = str(r["timestamp"])[11:16]
            print(f"{ts:<6} {r['open']:>9.2f} {r['high']:>9.2f} {r['low']:>9.2f} "
                  f"{r['close']:>9.2f} {r['volume']:>12,}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Download Upstox NSE instruments JSON and rebuild config/upstox_instruments.json.

Runs daily (or on demand) to keep instrument_key mappings fresh.
No auth required — this is a public BOD file refreshed by Upstox at ~6 AM IST.

Usage:
    python3 scripts/update_instruments.py
"""
import gzip
import json
import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
OUTPUT_PATH = Path(__file__).parent.parent / "config" / "upstox_instruments.json"


def download_instruments() -> list[dict]:
    """Download and decompress the Upstox NSE instruments JSON."""
    import urllib.request
    logger.info("Downloading %s", INSTRUMENTS_URL)
    with urllib.request.urlopen(INSTRUMENTS_URL, timeout=30) as resp:
        compressed = resp.read()
    data = gzip.decompress(compressed)
    instruments = json.loads(data)
    logger.info("Downloaded %d instruments", len(instruments))
    return instruments


def build_map(instruments: list[dict]) -> dict[str, str]:
    """Build trading_symbol → instrument_key map for NSE_EQ segment only."""
    result = {}
    for inst in instruments:
        if inst.get("segment") != "NSE_EQ":
            continue
        if inst.get("instrument_type") != "EQ":
            continue
        symbol = inst.get("trading_symbol", "").strip()
        ikey = inst.get("instrument_key", "").strip()
        if symbol and ikey:
            result[symbol] = ikey
    logger.info("Built map with %d NSE_EQ instruments", len(result))
    return result


def main():
    try:
        instruments = download_instruments()
    except Exception as e:
        logger.error("Failed to download instruments: %s", e)
        sys.exit(1)

    symbol_map = build_map(instruments)

    if not symbol_map:
        logger.error("Empty instrument map — aborting to avoid overwriting existing config")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(symbol_map, f, indent=2, sort_keys=True)

    logger.info("Saved %d symbols to %s", len(symbol_map), OUTPUT_PATH)

    # Spot-check a few known symbols
    for sym in ["RELIANCE", "INFY", "LT", "L&T", "BAJAJ-AUTO", "M&M", "ETERNAL"]:
        if sym in symbol_map:
            logger.info("  %-15s → %s", sym, symbol_map[sym])
        else:
            logger.warning("  %-15s → NOT FOUND", sym)


if __name__ == "__main__":
    main()

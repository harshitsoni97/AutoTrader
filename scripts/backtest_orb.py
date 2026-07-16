#!/usr/bin/env python3
"""Opening-Range-Breakout (ORB) backtest — a REAL intraday trader's setup.

Unlike the composite-selection backtest (which picks a stock pre-market from
daily bars and holds — proven to be a coin-flip), this tests a live-structure
setup the way an intraday trader actually trades it:

  - Mark the opening range (OR): high/low of the first `--or-min` minutes.
  - After the OR, LONG the first 5-min candle that closes above OR-high WITH
    volume > `--vol-mult` × the OR's average candle volume (breakout + conviction).
  - Stop = OR low. Target = entry + `--rr` × OR range. Else square off at close.
  - One trade per symbol per day. Adverse slippage on entry/exit.

Reports bootstrap-significance (95% CI on mean per-trade %, P(>0)) vs a naive
baseline (buy at OR-end, hold to close on the same signal days), and a
train/val split for stability. An edge whose CI straddles 0 is NOT real.

Free data only: Upstox 5-min intraday candles (analytics token). Fetched per
symbol in monthly chunks. Start small (default ~liquid large caps).

Usage:
  PYTHONPATH=src python scripts/backtest_orb.py --months 6 --or-min 15 --vol-mult 1.2 --rr 1.0
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
from pathlib import Path as _Path
_env = _Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env, override=False)
    except ImportError:
        for _l in _env.read_text().splitlines():
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _, _v = _l.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("orb")

_spec = importlib.util.spec_from_file_location(
    "run_backtest", os.path.join(os.path.dirname(__file__), "run_backtest.py"))
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)  # for bootstrap_ci + symbol lists

from autotrader.tools import upstox_data

# Focused, liquid default universe (tight ORB spreads). Override with --universe broad.
ORB_SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK",
    "LT", "ITC", "BHARTIARTL", "HINDUNILVR", "MARUTI", "TATAMOTORS", "TATASTEEL",
    "SUNPHARMA", "BAJFINANCE", "HCLTECH", "WIPRO", "ADANIPORTS", "TITAN", "ONGC",
    "POWERGRID", "NTPC", "COALINDIA", "JSWSTEEL", "DLF", "GODREJPROP", "DIVISLAB", "BEL",
]


def simulate_orb(candles: list[dict], or_min: int, interval: int, vol_mult: float,
                 rr: float, slip_bps: float) -> dict | None:
    """One day of ORB. candles = that day's intraday bars, ascending. None if no setup."""
    if not candles or len(candles) < (or_min // interval) + 2:
        return None
    candles = sorted(candles, key=lambda c: c["timestamp"])
    n_or = max(1, or_min // interval)
    or_bars = candles[:n_or]
    or_high = max(c["high"] for c in or_bars)
    or_low = min(c["low"] for c in or_bars)
    or_rng = or_high - or_low
    if or_rng <= 0:
        return None
    or_avg_vol = sum(c.get("volume", 0) for c in or_bars) / len(or_bars)
    slip = slip_bps / 10000.0

    # Find the breakout: first post-OR bar closing above OR-high with volume conviction.
    entry_idx = None
    for i in range(n_or, len(candles)):
        c = candles[i]
        if c["close"] > or_high and c.get("volume", 0) > vol_mult * max(1.0, or_avg_vol):
            entry_idx = i
            break
    if entry_idx is None:
        return {"setup": False, "pnl_pct": None}

    entry = or_high * (1 + slip)          # enter on the break, adverse slippage
    stop = or_low
    target = entry + rr * or_rng

    for c in candles[entry_idx + 1:]:
        if c["low"] <= stop:              # worst-case: stop before target within a bar
            exit_px = stop * (1 - slip)
            return {"setup": True, "outcome": "stop", "pnl_pct": round((exit_px / entry - 1) * 100, 4),
                    "entry": entry, "or_rng": or_rng}
        if c["high"] >= target:
            exit_px = target * (1 - slip)
            return {"setup": True, "outcome": "target", "pnl_pct": round((exit_px / entry - 1) * 100, 4),
                    "entry": entry, "or_rng": or_rng}
    exit_px = candles[-1]["close"] * (1 - slip)   # square off at close
    return {"setup": True, "outcome": "close", "pnl_pct": round((exit_px / entry - 1) * 100, 4),
            "entry": entry, "or_rng": or_rng}


def naive_from_or(candles: list[dict], or_min: int, interval: int, slip_bps: float) -> float | None:
    """Buy at OR-end close, hold to day close — baseline on the same day."""
    candles = sorted(candles, key=lambda c: c["timestamp"])
    n_or = max(1, or_min // interval)
    if len(candles) <= n_or:
        return None
    slip = slip_bps / 10000.0
    fill = candles[n_or - 1]["close"] * (1 + slip)
    return round((candles[-1]["close"] * (1 - slip) / fill - 1) * 100, 4)


def _fetch_intraday(ikey: str, months: int, interval: int) -> list[dict]:
    """Fetch intraday candles over `months`, chunked monthly (intraday range caps)."""
    today = date.today()
    out: list[dict] = []
    start = today - timedelta(days=months * 31)
    cur = start
    while cur < today:
        nxt = min(cur + timedelta(days=30), today)
        rows = upstox_data.get_historical_candles(ikey, "minutes", interval,
                                                  cur.isoformat(), nxt.isoformat())
        if rows:
            out.extend(rows)
        cur = nxt + timedelta(days=1)
    return out


def _by_day(rows: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for r in rows:
        d = str(r.get("timestamp", ""))[:10]
        if d:
            days.setdefault(d, []).append(r)
    return days


def _summarize(pnls: list[float], label: str) -> dict:
    if not pnls:
        logger.info("  %-18s | no trades", label)
        return {"trades": 0}
    wins = sum(1 for p in pnls if p > 0)
    ci = rb.bootstrap_ci(pnls)
    sig = "SIGNIFICANT" if ci["lo"] > 0 else "not significant (CI straddles 0)"
    logger.info("  %-18s | trades=%d win=%.1f%% avg=%.3f%% total=%.2f%% | 95%% CI [%+.3f,%+.3f]%% P(>0)=%.0f%% → %s",
                label, len(pnls), wins / len(pnls) * 100, sum(pnls) / len(pnls), sum(pnls),
                ci["lo"], ci["hi"], ci["prob_positive"] * 100, sig)
    return {"trades": len(pnls), "win_rate": round(wins / len(pnls), 4),
            "avg_pnl_pct": round(sum(pnls) / len(pnls), 4), "total_pnl_pct": round(sum(pnls), 4),
            "ci": ci, "significant": ci["lo"] > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--interval", type=int, default=5, help="candle minutes (5 default)")
    ap.add_argument("--or-min", type=int, default=15, help="opening-range minutes")
    ap.add_argument("--vol-mult", type=float, default=1.2, help="breakout volume vs OR avg")
    ap.add_argument("--rr", type=float, default=1.0, help="target = entry + rr*OR range")
    ap.add_argument("--slip-bps", type=float, default=3.0)
    ap.add_argument("--universe", choices=["orb", "broad"], default="orb")
    ap.add_argument("--out", default="reports/orb_backtest.json")
    args = ap.parse_args()

    os.makedirs("reports", exist_ok=True)
    if not os.environ.get("UPSTOX_ANALYTICS_TOKEN"):
        logger.error("UPSTOX_ANALYTICS_TOKEN not set (checked .env)."); sys.exit(1)

    map_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../config/upstox_instruments.json"))
    with open(map_path) as f:
        full_map = json.load(f)
    universe = rb.BACKTEST_SYMBOLS_BROAD if args.universe == "broad" else ORB_SYMBOLS
    imap = {s: full_map[s] for s in universe if s in full_map}
    logger.info("ORB universe: %d symbols | interval=%dm OR=%dm vol×%.1f rr=%.1f",
                len(imap), args.interval, args.or_min, args.vol_mult, args.rr)

    trades = []   # (date, symbol, pnl, outcome)
    naive = []    # (date, naive_pnl)
    for si, (sym, ikey) in enumerate(imap.items(), 1):
        rows = _fetch_intraday(ikey, args.months, args.interval)
        days = _by_day(rows)
        logger.info("  [%d/%d] %s: %d days", si, len(imap), sym, len(days))
        for d, dc in days.items():
            res = simulate_orb(dc, args.or_min, args.interval, args.vol_mult, args.rr, args.slip_bps)
            if not res or not res.get("setup"):
                continue
            trades.append({"date": d, "symbol": sym, "pnl": res["pnl_pct"], "outcome": res["outcome"]})
            nv = naive_from_or(dc, args.or_min, args.interval, args.slip_bps)
            naive.append({"date": d, "pnl": nv})

    logger.info("\n=== ORB BACKTEST (%d symbols, %d mo, %d setups) ===", len(imap), args.months, len(trades))
    if not trades:
        logger.info("No ORB setups found."); return
    trades.sort(key=lambda t: t["date"])
    pnls = [t["pnl"] for t in trades]
    npnls = [n["pnl"] for n in naive if n["pnl"] is not None]

    orb_all = _summarize(pnls, "ORB (all)")
    _summarize(npnls, "naive (all)")
    if pnls and npnls and len(pnls) == len(npnls):
        _summarize([a - b for a, b in zip(pnls, npnls)], "Δ(ORB-naive)")

    # Train/val stability split (first 70% of days vs last 30%).
    split = int(len(trades) * 0.7)
    logger.info("── stability split ──")
    _summarize([t["pnl"] for t in trades[:split]], "ORB train(70%)")
    _summarize([t["pnl"] for t in trades[split:]], "ORB val(30%)")

    # Outcome mix
    from collections import Counter
    mix = Counter(t["outcome"] for t in trades)
    logger.info("outcomes: %s", dict(mix))

    with open(args.out, "w") as f:
        json.dump({"run_date": date.today().isoformat(), "params": vars(args),
                   "orb_all": orb_all, "trades": trades}, f, indent=2, default=str)
    logger.info("Report → %s", args.out)


if __name__ == "__main__":
    main()

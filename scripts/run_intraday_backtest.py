#!/usr/bin/env python3
"""Intraday backtest — simulate the ACTUAL trading flow on 30-min candles.

The daily backtest (run_backtest.py) only tests *stock selection* with a naive
next-day hold. This one tests the machinery that actually generates our edge (or
doesn't): book at the open + slippage, re-anchor stop/targets to the fill, scale
half at T1, run the rest to T2, stop out, or square off at the close.

Flow per pick (mirrors the live entry + monitoring agents):
  1. Pre-market plan from daily data: entry≈prev close, stop=entry−ATR·mult,
     T1=entry+ATR·mult (1R), T2=entry+ATR·mult·rr.
  2. Entry guards at the open (skip overextended / below-stop / >1.5 ATR chase).
  3. Book at the first 30-min open + slippage; RE-ANCHOR stop/T1/T2 to the fill
     (the live fix) so R:R is preserved.
  4. Walk 30-min bars, worst-case ordering (stop checked before target in a bar):
     stop → exit all; T1 → book half, run the rest; T2 → exit runner.
  5. Square off any remainder at the last bar's close.

Two arms, both bootstrap-CI'd:
  • machinery  — the flow above.
  • naive      — buy at the open, sell at the close (same picks). Isolates what
                 the stop/scale machinery adds over just being long the pick.

Usage:
  PYTHONPATH=src python scripts/run_intraday_backtest.py --months 12 --universe broad
Needs UPSTOX_ANALYTICS_TOKEN (loaded from .env). Intraday candles are fetched
only for the picked symbol-days (~one API call per validation day), so it's cheap.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

# Load .env so the token is available on a manual run.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("intraday_backtest")

# Reuse the daily backtest's indicator/scoring machinery.
_spec = importlib.util.spec_from_file_location(
    "run_backtest", os.path.join(os.path.dirname(__file__), "run_backtest.py"))
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

from autotrader.tools import upstox_data


def simulate_intraday(candles: list[dict], plan_entry: float, atr: float,
                      stop_mult: float, rr: float, slip_bps: float = 4.0,
                      max_ext_atr: float = 1.5) -> dict:
    """Simulate one day's plan→book→manage flow on 30-min candles."""
    if not candles or atr <= 0 or plan_entry <= 0:
        return {"outcome": "no_data", "pnl_pct": 0.0}
    candles = sorted(candles, key=lambda c: c["timestamp"])
    open_px = candles[0]["open"]
    slip = slip_bps / 10000.0

    stop_dist = atr * stop_mult
    plan_stop = plan_entry - stop_dist
    plan_t1 = plan_entry + stop_dist
    plan_t2 = plan_entry + stop_dist * rr

    # Entry guards (mirror entry.py), evaluated on the actual open.
    if open_px >= plan_t1:
        return {"outcome": "skip_overextended", "pnl_pct": 0.0}
    if open_px <= plan_stop:
        return {"outcome": "skip_below_stop", "pnl_pct": 0.0}
    if (open_px - plan_entry) > max_ext_atr * atr:
        return {"outcome": "skip_extended", "pnl_pct": 0.0}

    # Book at the open + adverse slippage; re-anchor levels to the fill.
    fill = open_px * (1 + slip)
    stop = fill - stop_dist
    t1 = fill + stop_dist
    t2 = fill + stop_dist * rr

    pos = 1.0            # fraction of position still held
    realized = 0.0       # price points per share (for the full 1.0 position)
    half_done = False
    outcome = "open"

    for c in candles:
        hi, lo = c["high"], c["low"]
        if lo <= stop:                                   # worst-case: stop first
            realized += pos * (stop * (1 - slip) - fill)
            pos = 0.0
            outcome = "stop" if not half_done else "t1_then_stop"
            break
        if not half_done and hi >= t1:                   # scale half at T1
            realized += 0.5 * (t1 * (1 - slip) - fill)
            pos = 0.5
            half_done = True
            continue                                     # one action per bar
        if half_done and hi >= t2:                       # runner hits T2
            realized += pos * (t2 * (1 - slip) - fill)
            pos = 0.0
            outcome = "t2"
            break

    if pos > 0:                                          # square off at the close
        realized += pos * (candles[-1]["close"] * (1 - slip) - fill)
        outcome = "t1_then_close" if half_done else "close"

    return {"outcome": outcome, "pnl_pct": round(realized / fill * 100, 4),
            "fill": round(fill, 2), "open": open_px}


def naive_hold(candles: list[dict], slip_bps: float = 4.0) -> dict:
    """Buy at the open, sell at the close (same slippage). Baseline arm."""
    if not candles:
        return {"outcome": "no_data", "pnl_pct": 0.0}
    candles = sorted(candles, key=lambda c: c["timestamp"])
    slip = slip_bps / 10000.0
    fill = candles[0]["open"] * (1 + slip)
    exit_px = candles[-1]["close"] * (1 - slip)
    return {"outcome": "close", "pnl_pct": round((exit_px - fill) / fill * 100, 4)}


def _summarize(pnls: list[float], label: str) -> dict:
    if not pnls:
        logger.info("  %-10s | no trades", label)
        return {"trades": 0}
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    ci = rb.bootstrap_ci(pnls)
    sig = "SIGNIFICANT" if ci["lo"] > 0 else "not significant (CI straddles 0)"
    logger.info("  %-10s | trades=%d win=%.1f%% avg=%.3f%% total=%.2f%% | "
                "95%% CI [%+.3f, %+.3f]%% P(>0)=%.0f%% → %s",
                label, len(pnls), wins / len(pnls) * 100, total / len(pnls),
                total, ci["lo"], ci["hi"], ci["prob_positive"] * 100, sig)
    return {"trades": len(pnls), "win_rate": round(wins / len(pnls), 4),
            "avg_pnl_pct": round(total / len(pnls), 4), "total_pnl_pct": round(total, 4),
            "ci": ci, "significant": ci["lo"] > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--universe", choices=["curated", "broad"], default="broad")
    ap.add_argument("--min-score", type=int, default=62, help="Eligibility floor (live default).")
    ap.add_argument("--stop-mult", type=float, default=1.0)
    ap.add_argument("--target-rr", type=float, default=1.5)
    ap.add_argument("--slip-bps", type=float, default=4.0)
    ap.add_argument("--out", default="reports/intraday_backtest.json")
    args = ap.parse_args()

    os.makedirs("reports", exist_ok=True)
    if not os.environ.get("UPSTOX_ANALYTICS_TOKEN"):
        logger.error("UPSTOX_ANALYTICS_TOKEN not set (checked .env). Export it and retry.")
        sys.exit(1)

    map_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../config/upstox_instruments.json"))
    with open(map_path) as f:
        full_map = json.load(f)
    universe = rb.BACKTEST_SYMBOLS_BROAD if args.universe == "broad" else rb.BACKTEST_SYMBOLS
    instrument_map = {s: full_map[s] for s in universe if s in full_map}
    logger.info("Universe: %d symbols (%s)", len(instrument_map), args.universe)

    # 1. Daily data → indicators, regime, and per-day pick (enhanced formula).
    all_data = rb.fetch_all_candles(instrument_map, args.months)
    nifty_rows = rb.fetch_nifty_candles(args.months)
    trading_days = sorted({r["timestamp"][:10] for rows in all_data.values() for r in rows})
    split = int(len(trading_days) * 0.70)
    val_days = list(range(split, len(trading_days) - 1))
    logger.info("Trading days: %d | validation days: %d", len(trading_days), len(val_days))

    cache = rb.precompute_indicators(all_data, trading_days)
    regime_map = rb.build_regime_map(nifty_rows)

    picks = []  # (trade_day, symbol, plan_entry, atr)
    for di in val_days:
        if di < 1:
            continue
        # NO LOOK-AHEAD: the pre-market plan is built BEFORE today opens, from data
        # through YESTERDAY's close. So score as of trading_days[di-1] (which holds
        # yesterday's close as its indicators), then trade trading_days[di] intraday.
        signal_day = trading_days[di - 1]
        day = trading_days[di]
        cands = rb.get_day_candidates_from_cache(cache, signal_day, 20, 50)
        if not cands:
            continue
        reg_label, reg_score, conf = regime_map.get(signal_day, ("range_bound", 60.0, 0.6))
        scored = []
        for c in cands:
            s = rb.composite_score_enhanced(c, reg_label, reg_score, conf)
            if s >= args.min_score:
                scored.append((s, c))
        if not scored:
            continue
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][1]
        picks.append((day, top["symbol"], top["close"], top["atr"]))

    logger.info("Picks over validation window: %d", len(picks))

    # 2. Fetch that day's 30-min candles per pick and simulate both arms.
    mach_pnls, naive_pnls, skips = [], [], 0
    trade_log = []
    for day, sym, plan_entry, atr in picks:
        ikey = instrument_map.get(sym)
        candles = upstox_data.get_historical_candles(ikey, "minutes", 30, day, day)
        candles = [c for c in (candles or []) if str(c.get("timestamp", "")).startswith(day)]
        if not candles:
            skips += 1
            continue
        m = simulate_intraday(candles, plan_entry, atr, args.stop_mult, args.target_rr, args.slip_bps)
        n = naive_hold(candles, args.slip_bps)
        if m["outcome"].startswith("skip"):
            # Machinery declined to enter — that's a real (0-P&L, no-trade) decision;
            # exclude from the trade sample but record it.
            trade_log.append({"date": day, "symbol": sym, **m})
            continue
        mach_pnls.append(m["pnl_pct"])
        naive_pnls.append(n["pnl_pct"])
        trade_log.append({"date": day, "symbol": sym, "machinery": m["pnl_pct"],
                          "naive": n["pnl_pct"], "outcome": m["outcome"]})

    logger.info("\n=== INTRADAY BACKTEST (held-out %d days, %s universe) ===",
                len(val_days), args.universe)
    logger.info("  entries taken=%d | skipped-at-open=%d | no-candle=%d",
                len(mach_pnls), sum(1 for t in trade_log if t.get("outcome", "").startswith("skip")), skips)
    mach = _summarize(mach_pnls, "machinery")
    naive = _summarize(naive_pnls, "naive")
    if mach_pnls and naive_pnls:
        edge = [m - n for m, n in zip(mach_pnls, naive_pnls)]
        _summarize(edge, "Δ(mach-naive)")

    report = {"run_date": date.today().isoformat(), "months": args.months,
              "universe": args.universe, "symbols": len(instrument_map),
              "val_days": len(val_days), "picks": len(picks),
              "params": {"min_score": args.min_score, "stop_mult": args.stop_mult,
                         "target_rr": args.target_rr, "slip_bps": args.slip_bps},
              "machinery": mach, "naive": naive, "trade_log": trade_log}
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Report → %s", args.out)


if __name__ == "__main__":
    main()

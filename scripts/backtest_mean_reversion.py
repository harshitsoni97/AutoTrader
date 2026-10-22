#!/usr/bin/env python3
"""Backtest the mean-reversion (oversold-bounce) signal on NSE daily history.

Same rigor as backtest_orb.py — bootstrap CIs, transaction costs, a naive baseline, and
LOOK-AHEAD-SAFE entry (signal detected on bar i's close -> enter at bar i+1's OPEN). This
is the gate: mean-reversion only joins the live book if it shows a real, cost-surviving,
statistically-significant edge here — and ideally a different return profile from ORB
(it fires on oversold capitulation, ORB on breakouts, so they should rarely trade the
same name the same day).

Run on OCI (needs Upstox history):
    PYTHONPATH=src ./venv/bin/python scripts/backtest_mean_reversion.py --months 12 --cost-bps 10
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pathlib import Path
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv; load_dotenv(_env, override=True)
    except ImportError:
        for ln in _env.read_text().splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("="); os.environ[k.strip()] = v.strip()

import json, structlog
from datetime import date, timedelta

from autotrader.tools import upstox_data
from autotrader.strategies.mean_reversion import oversold_bounce_signal, MeanRevConfig
import run_backtest as rb  # bootstrap_ci, symbol lists

from autotrader.core.logging_setup import configure_logging
configure_logging()
logger = structlog.get_logger()


def simulate_symbol(daily: list[dict], cfg: MeanRevConfig, max_hold: int,
                    cost_bps: float) -> list[dict]:
    """Walk the daily series; on each oversold-bounce signal, enter NEXT bar's open and
    manage to target(mean) / stop / max_hold. Returns a list of trade dicts (pnl_pct)."""
    rows = sorted(daily, key=lambda c: c.get("timestamp", ""))
    cost = cost_bps / 10000.0
    trades: list[dict] = []
    i = 40                      # warmup for RSI/SMA
    n = len(rows)
    while i < n - 1:
        sig = oversold_bounce_signal(rows[: i + 1], cfg)   # uses bars UP TO i (close known)
        if not sig:
            i += 1
            continue
        entry = rows[i + 1].get("open") or rows[i + 1]["close"]   # look-ahead safe
        entry = float(entry) * (1 + cost)
        stop, target = sig["stop"], sig["target1"]
        exit_px, reason, held = None, "time", 0
        for j in range(i + 1, min(i + 1 + max_hold, n)):
            held = j - i
            lo = float(rows[j].get("low", rows[j]["close"]))
            hi = float(rows[j].get("high", rows[j]["close"]))
            if lo <= stop:
                exit_px, reason = stop, "stop"; break
            if hi >= target:
                exit_px, reason = target, "target"; break
            exit_px = float(rows[j]["close"])
        exit_px = exit_px * (1 - cost)
        trades.append({"entry": round(entry, 2), "exit": round(exit_px, 2),
                       "pnl_pct": round((exit_px / entry - 1) * 100, 4), "reason": reason,
                       "held": held, "rsi": sig["rsi"], "date": str(rows[i].get("timestamp", ""))[:10]})
        i = i + held + 1        # no overlapping trades on the same name
    return trades


def naive_oversold(daily: list[dict], cfg: MeanRevConfig, max_hold: int, cost_bps: float) -> list[dict]:
    """Baseline: buy on oversold WITHOUT the bounce trigger (catch the knife), same exits.
    If the bounce requirement adds nothing, this baseline matches the real signal."""
    rows = sorted(daily, key=lambda c: c.get("timestamp", ""))
    cost = cost_bps / 10000.0
    closes = [float(r["close"]) for r in rows]
    out: list[dict] = []
    i = 40
    while i < len(rows) - 1:
        from autotrader.strategies.mean_reversion import rsi as _rsi
        r = _rsi(closes[: i + 1], cfg.rsi_period)
        if r is None or r > cfg.rsi_oversold:
            i += 1; continue
        entry = float(rows[i + 1].get("open") or rows[i + 1]["close"]) * (1 + cost)
        target = sum(closes[i + 1 - cfg.sma_period:i + 1]) / cfg.sma_period
        exit_px, held = None, 0
        for j in range(i + 1, min(i + 1 + max_hold, len(rows))):
            held = j - i
            if float(rows[j].get("high", rows[j]["close"])) >= target:
                exit_px = target; break
            exit_px = float(rows[j]["close"])
        out.append({"pnl_pct": round((exit_px * (1 - cost) / entry - 1) * 100, 4)})
        i = i + held + 1
    return out


def _summarize(pnls: list[float], label: str) -> dict:
    if not pnls:
        logger.info("  %-20s | no trades", label); return {"trades": 0}
    wins = sum(1 for p in pnls if p > 0)
    ci = rb.bootstrap_ci(pnls)
    sig = "SIGNIFICANT" if ci["lo"] > 0 else "not significant (CI straddles 0)"
    logger.info("  %-20s | trades=%d win=%.1f%% avg=%.3f%% total=%.1f%% | 95%% CI [%+.3f,%+.3f]%% P(>0)=%.0f%% -> %s",
                label, len(pnls), wins/len(pnls)*100, sum(pnls)/len(pnls), sum(pnls),
                ci["lo"], ci["hi"], ci["prob_positive"]*100, sig)
    return {"trades": len(pnls), "win_rate": round(wins/len(pnls), 4),
            "avg_pnl_pct": round(sum(pnls)/len(pnls), 4), "total_pnl_pct": round(sum(pnls), 4),
            "ci": ci, "significant": ci["lo"] > 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--max-hold", type=int, default=5, help="max days to hold a reversion trade")
    ap.add_argument("--rsi-oversold", type=float, default=30.0)
    ap.add_argument("--cost-bps", type=float, default=10.0, help="round-trip cost bps (10=0.10%%)")
    ap.add_argument("--universe", choices=["core", "broad"], default="broad")
    ap.add_argument("--out", default="reports/mean_reversion_backtest.json")
    args = ap.parse_args()
    os.makedirs("reports", exist_ok=True)

    cfg = MeanRevConfig(rsi_oversold=args.rsi_oversold)
    universe = rb.BACKTEST_SYMBOLS_BROAD if args.universe == "broad" else rb.BACKTEST_SYMBOLS
    today = date.today()
    frm = (today - timedelta(days=args.months * 31)).isoformat()

    all_tr, naive_pnls = [], []
    from autotrader.agents.layer2.technical_structure import _load_instrument_map
    imap = _load_instrument_map()
    logger.info("mean_reversion_backtest_start symbols=%d months=%d cost_bps=%.0f",
                len(universe), args.months, args.cost_bps)
    for sym in universe:
        ikey = imap.get(sym)
        if not ikey:
            continue
        daily = upstox_data.get_historical_candles(ikey, "days", 1, frm, today.isoformat())
        if not daily or len(daily) < 60:
            continue
        tr = simulate_symbol(daily, cfg, args.max_hold, args.cost_bps)
        all_tr.extend(tr)
        naive_pnls.extend(t["pnl_pct"] for t in naive_oversold(daily, cfg, args.max_hold, args.cost_bps))

    pnls = [t["pnl_pct"] for t in all_tr]
    logger.info("=== Mean-Reversion (oversold BOUNCE) — cost %.0f bps ===", args.cost_bps)
    real = _summarize(pnls, "oversold-bounce")
    logger.info("=== Baseline (oversold, NO bounce — catch the knife) ===")
    naive = _summarize(naive_pnls, "oversold-no-bounce")
    edge = (real.get("avg_pnl_pct", 0) - naive.get("avg_pnl_pct", 0))
    logger.info("Bounce-trigger edge vs naive: %+.3f%%/trade", edge)

    out = {"params": vars(args), "real": real, "naive": naive, "bounce_edge_vs_naive": round(edge, 4),
           "n_trades": len(pnls), "avg_hold_days": round(sum(t["held"] for t in all_tr)/max(1, len(all_tr)), 2)}
    Path(args.out).write_text(json.dumps(out, indent=2))
    logger.info("wrote %s", args.out)
    verdict = ("JOIN THE BOOK" if real.get("significant") and edge > 0
               else "DO NOT ADD (no significant cost-surviving edge over the naive baseline)")
    logger.info("VERDICT: %s", verdict)


if __name__ == "__main__":
    main()

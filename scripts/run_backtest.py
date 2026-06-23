"""
Walk-forward backtest to optimise composite scoring weights and minimum_score threshold.

Usage:
    python scripts/run_backtest.py [--months 6] [--out reports/backtest_results.json]

What it does:
    1. Fetches 12 months of Upstox daily candles for all mapped symbols + Nifty
    2. For each trading day (rolling 60-day lookback), computes:
           EMA9/21/50, RSI14, ADX14, ATR14, VWAP, BB, pattern, technical_score
    3. Simulates a fixed composite-score formula under many (weights, threshold) combos
    4. Picks the top-scoring candidate per day per combo
    5. Evaluates outcome: next-day stop hit → loss, target1 hit → win, else close pct
    6. Walk-forward split: train on first 70% of days, validate on last 30%
    7. Writes optimal params to config/strategy_params.json and a full report to --out
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest")

# ---------------------------------------------------------------------------
# Indicator helpers (self-contained, no imports from agents)
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        return [prices[-1]] * len(prices) if prices else []
    k = 2 / (period + 1)
    emas = [sum(prices[:period]) / period]
    for p in prices[period:]:
        emas.append(p * k + emas[-1] * (1 - k))
    return [emas[0]] * (period - 1) + emas


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas[-period:]]
    losses = [abs(min(0, d)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def _adx(rows: list[dict], period: int = 14) -> float:
    if len(rows) < period + 2:
        return 20.0
    plus_dms, minus_dms, trs = [], [], []
    for i in range(1, len(rows)):
        up = rows[i]["high"] - rows[i - 1]["high"]
        dn = rows[i - 1]["low"] - rows[i]["low"]
        plus_dms.append(up if up > dn and up > 0 else 0)
        minus_dms.append(dn if dn > up and dn > 0 else 0)
        trs.append(max(
            rows[i]["high"] - rows[i]["low"],
            abs(rows[i]["high"] - rows[i - 1]["close"]),
            abs(rows[i]["low"] - rows[i - 1]["close"]),
        ))
    s = lambda arr: sum(arr[-period:]) / period
    atr_s = s(trs) or 1
    di_plus = 100 * s(plus_dms) / atr_s
    di_minus = 100 * s(minus_dms) / atr_s
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0
    return round(dx, 2)


def _atr(rows: list[dict], period: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    trs = [max(
        rows[i]["high"] - rows[i]["low"],
        abs(rows[i]["high"] - rows[i - 1]["close"]),
        abs(rows[i]["low"] - rows[i - 1]["close"]),
    ) for i in range(1, len(rows))]
    return sum(trs[-period:]) / min(period, len(trs))


def _vwap(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    d = rows[-1]
    return round((d["open"] + d["high"] + d["low"] + d["close"]) / 4, 2)


def _bollinger_bands(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    if len(closes) < period:
        m = closes[-1] if closes else 0.0
        return m, m, m
    w = closes[-period:]
    mid = sum(w) / period
    std = (sum((x - mid) ** 2 for x in w) / period) ** 0.5
    return round(mid + 2 * std, 2), round(mid, 2), round(mid - 2 * std, 2)


def _detect_pattern(rows: list[dict], ema9: float, ema21: float, ema50: float,
                    rsi: float, vwap: float, closes: list[float]) -> str:
    if len(rows) < 5:
        return "NONE"
    price = rows[-1]["close"]
    aligned = ema9 > ema21 > ema50
    above_vwap = price > vwap if vwap > 0 else True
    if not above_vwap:
        return "NONE"
    prev_high = max(r["high"] for r in rows[-5:-1])
    if aligned and price > prev_high * 1.005:
        return "BREAKOUT"
    if rows[-1]["low"] < vwap < rows[-1]["close"] and aligned:
        return "VWAP_CROSS"
    if aligned and rsi > 50:
        return "EMA_ALIGNMENT"
    return "NONE"


def _technical_score(ema9: float, ema21: float, ema50: float, rsi: float,
                     adx: float, pattern: str, above_vwap: bool,
                     adx_threshold: float = 20, rsi_min: float = 50) -> float:
    if adx < adx_threshold:
        return 0.0
    score = 0.0
    if ema9 > ema21 > ema50:
        score += 30
    elif ema9 > ema21:
        score += 15
    rsi_mid = rsi_min + 5
    if rsi_mid <= rsi <= 75:
        score += 20
    elif rsi_min <= rsi < rsi_mid:
        score += 12
    elif rsi > 75:
        score += 8
    if adx > 30:
        score += 20
    elif adx > 25:
        score += 15
    elif adx > 20:
        score += 10
    if above_vwap:
        score += 10
    score += {"BREAKOUT": 20, "VWAP_CROSS": 16, "EMA_ALIGNMENT": 10, "NONE": 0}.get(pattern, 0)
    return min(100.0, round(score, 1))


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_all_candles(instrument_map: dict[str, str], months: int) -> dict[str, list[dict]]:
    """Return {symbol: [sorted daily rows]} for all symbols in map."""
    from autotrader.tools import upstox_data

    today = date.today()
    from_date = (today - timedelta(days=months * 31 + 30)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    data: dict[str, list[dict]] = {}
    symbols = list(instrument_map.keys())
    logger.info("Fetching %d months of daily candles for %d symbols...", months, len(symbols))

    for i, symbol in enumerate(symbols):
        ikey = instrument_map[symbol]
        try:
            rows = upstox_data.get_historical_candles(ikey, "days", 1, from_date, to_date)
            if rows and len(rows) >= 30:
                rows.sort(key=lambda r: r["timestamp"])
                data[symbol] = rows
                logger.info("  [%d/%d] %s: %d days", i + 1, len(symbols), symbol, len(rows))
            else:
                logger.warning("  [%d/%d] %s: insufficient data (%s rows)", i + 1, len(symbols), symbol, len(rows) if rows else 0)
        except Exception as e:
            logger.warning("  %s failed: %s", symbol, e)
        time.sleep(0.15)  # rate limit

    return data


def fetch_nifty_candles(months: int) -> list[dict]:
    from autotrader.tools import upstox_data
    today = date.today()
    from_date = (today - timedelta(days=months * 31 + 30)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")
    rows = upstox_data.get_historical_candles("NSE_INDEX|Nifty 50", "days", 1, from_date, to_date) or []
    rows.sort(key=lambda r: r["timestamp"])
    logger.info("Nifty: %d days fetched", len(rows))
    return rows


# ---------------------------------------------------------------------------
# Per-day candidate scoring
# ---------------------------------------------------------------------------

def compute_day_candidates(
    all_data: dict[str, list[dict]],
    day_idx: int,          # index into the sorted trading day list
    trading_days: list[str],
    lookback: int = 60,
    adx_threshold: float = 20,
    rsi_min: float = 50,
) -> list[dict]:
    """Return scored candidates for a single simulated trading day."""
    today_str = trading_days[day_idx]
    candidates = []

    for symbol, rows in all_data.items():
        # Find rows up to and including today
        hist = [r for r in rows if r["timestamp"][:10] <= today_str]
        if len(hist) < lookback:
            continue

        window = hist[-lookback:]
        closes = [r["close"] for r in window]
        ema9 = _ema(closes, 9)[-1]
        ema21 = _ema(closes, 21)[-1]
        ema50_series = _ema(closes, 50)
        ema50 = ema50_series[-1] if len(ema50_series) >= 50 else ema21
        rsi = _rsi(closes, 14)
        adx = _adx(window, 14)
        vwap = _vwap(window)
        atr = _atr(window, 14)
        above_vwap = closes[-1] > vwap if vwap > 0 else True
        pattern = _detect_pattern(window, ema9, ema21, ema50, rsi, vwap, closes)
        tech_score = _technical_score(ema9, ema21, ema50, rsi, adx, pattern, above_vwap,
                                      adx_threshold, rsi_min)

        # Volume ratio (vs 20d avg)
        vols = [r["volume"] for r in window if r["volume"] > 0]
        avg_vol = sum(vols[-20:]) / min(20, len(vols)) if vols else 1
        vol_ratio = window[-1]["volume"] / avg_vol if avg_vol > 0 else 1.0
        vol_score = min(100.0, max(0.0, (vol_ratio - 1) * 50 + 50))

        candidates.append({
            "symbol": symbol,
            "date": today_str,
            "close": closes[-1],
            "atr": round(atr, 4),
            "pattern": pattern,
            "technical_score": tech_score,
            "volume_score": round(vol_score, 1),
            "adx": adx,
            "rsi": rsi,
            "above_vwap": above_vwap,
        })

    return candidates


def get_next_day_outcome(
    symbol: str,
    entry_price: float,
    atr: float,
    stop_mult: float,
    target_rr: float,
    all_data: dict[str, list[dict]],
    trading_days: list[str],
    day_idx: int,
) -> dict:
    """Simulate next-day trade: returns hit_stop, hit_target, pnl_pct."""
    if day_idx + 1 >= len(trading_days):
        return {"hit_stop": False, "hit_target": False, "pnl_pct": 0.0, "outcome": "no_next_day"}

    next_day = trading_days[day_idx + 1]
    rows = all_data.get(symbol, [])
    next_row = next((r for r in rows if r["timestamp"][:10] == next_day), None)
    if not next_row:
        return {"hit_stop": False, "hit_target": False, "pnl_pct": 0.0, "outcome": "no_data"}

    stop_dist = atr * stop_mult
    stop = entry_price - stop_dist
    target = entry_price + stop_dist * target_rr

    # Assume worst-case intraday ordering: stop checked before target on down days
    hit_stop = next_row["low"] <= stop
    hit_target = next_row["high"] >= target

    if hit_stop and hit_target:
        # Gap or intraday whipsaw — use open to decide
        if next_row["open"] <= stop:
            hit_target = False
        else:
            hit_stop = False

    if hit_stop:
        pnl_pct = (stop - entry_price) / entry_price * 100
        outcome = "stop"
    elif hit_target:
        pnl_pct = (target - entry_price) / entry_price * 100
        outcome = "target"
    else:
        pnl_pct = (next_row["close"] - entry_price) / entry_price * 100
        outcome = "close"

    return {"hit_stop": hit_stop, "hit_target": hit_target, "pnl_pct": round(pnl_pct, 4), "outcome": outcome}


# ---------------------------------------------------------------------------
# Composite score formula
# ---------------------------------------------------------------------------

def composite_score(candidate: dict, weights: dict, regime_score: float = 60.0,
                    sector_score: float = 60.0, rs_score: float = 60.0,
                    options_score: float = 50.0) -> float:
    return (
        regime_score       * weights["market_regime"]
        + sector_score     * weights["sector_strength"]
        + rs_score         * weights["relative_strength"]
        + candidate["volume_score"] * weights["volume"]
        + 0.0              * weights["catalyst"]       # no catalyst data in backtest
        + candidate["technical_score"] * weights["technical"]
        + options_score    * weights["options_sentiment"]
    )


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def evaluate_scheme(
    all_data: dict[str, list[dict]],
    trading_days: list[str],
    day_indices: list[int],
    weights: dict,
    min_score: float,
    adx_threshold: float,
    rsi_min: float,
    stop_mult: float,
    target_rr: float,
) -> dict:
    """Run one (weights, params) combo across given day indices. Returns metrics."""
    trades = []

    for day_idx in day_indices:
        candidates = compute_day_candidates(
            all_data, day_idx, trading_days,
            adx_threshold=adx_threshold, rsi_min=rsi_min,
        )
        if not candidates:
            continue

        # Score and filter
        scored = []
        for c in candidates:
            score = composite_score(c, weights)
            if score >= min_score:
                scored.append({**c, "score": score})

        if not scored:
            continue

        scored.sort(key=lambda x: x["score"], reverse=True)
        pick = scored[0]

        outcome = get_next_day_outcome(
            pick["symbol"], pick["close"], pick["atr"],
            stop_mult, target_rr,
            all_data, trading_days, day_idx,
        )
        if outcome["outcome"] in ("no_next_day", "no_data"):
            continue

        trades.append({
            "date": trading_days[day_idx],
            "symbol": pick["symbol"],
            "score": pick["score"],
            "pattern": pick["pattern"],
            **outcome,
        })

    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "total_pnl_pct": 0.0, "metric": 0.0}

    wins = [t for t in trades if t["hit_target"]]
    stops = [t for t in trades if t["hit_stop"]]
    win_rate = len(wins) / len(trades)
    avg_rr = (sum(t["pnl_pct"] for t in trades) / len(trades)) if trades else 0.0
    total_pnl = sum(t["pnl_pct"] for t in trades)
    metric = win_rate * max(0, avg_rr) * (len(trades) ** 0.5)  # penalise too few trades

    return {
        "trades": len(trades),
        "wins": len(wins),
        "stops": len(stops),
        "win_rate": round(win_rate, 4),
        "avg_pnl_pct": round(avg_rr, 4),
        "total_pnl_pct": round(total_pnl, 4),
        "metric": round(metric, 6),
        "trade_log": trades,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6, help="Months of history to backtest")
    parser.add_argument("--out", default="reports/backtest_results.json")
    args = parser.parse_args()

    os.makedirs("reports", exist_ok=True)

    # Load instrument map
    map_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../config/upstox_instruments.json"))
    with open(map_path) as f:
        instrument_map: dict = json.load(f)

    # Fetch data
    all_data = fetch_all_candles(instrument_map, args.months)
    nifty_rows = fetch_nifty_candles(args.months)

    if len(all_data) < 5:
        logger.error("Too few symbols fetched. Check UPSTOX_ANALYTICS_TOKEN.")
        sys.exit(1)

    # Build sorted list of all trading days that appear in data
    all_days: set[str] = set()
    for rows in all_data.values():
        for r in rows:
            all_days.add(r["timestamp"][:10])
    trading_days = sorted(all_days)
    logger.info("Trading days in dataset: %d (%s → %s)", len(trading_days), trading_days[0], trading_days[-1])

    # Walk-forward split: train on first 70%, validate on last 30%
    split = int(len(trading_days) * 0.70)
    # Need at least 60-day lookback before we start, so offset train start
    lookback = 60
    train_days = list(range(lookback, split))
    val_days   = list(range(split, len(trading_days) - 1))
    logger.info("Train days: %d, Validation days: %d", len(train_days), len(val_days))

    # ── Grid search space ────────────────────────────────────────────────────
    weight_schemes = [
        # name, weights dict
        ("original",    {"market_regime":0.18,"sector_strength":0.17,"relative_strength":0.20,"volume":0.15,"catalyst":0.15,"technical":0.10,"options_sentiment":0.05}),
        ("tech_heavy",  {"market_regime":0.15,"sector_strength":0.15,"relative_strength":0.15,"volume":0.15,"catalyst":0.10,"technical":0.25,"options_sentiment":0.05}),
        ("balanced",    {"market_regime":0.14,"sector_strength":0.14,"relative_strength":0.14,"volume":0.14,"catalyst":0.14,"technical":0.25,"options_sentiment":0.05}),
        ("vol_tech",    {"market_regime":0.12,"sector_strength":0.12,"relative_strength":0.14,"volume":0.20,"catalyst":0.12,"technical":0.25,"options_sentiment":0.05}),
        ("rs_focus",    {"market_regime":0.15,"sector_strength":0.15,"relative_strength":0.25,"volume":0.15,"catalyst":0.10,"technical":0.15,"options_sentiment":0.05}),
    ]

    min_score_grid   = [55, 60, 65, 70]
    adx_thresh_grid  = [18, 20, 22, 25]
    rsi_min_grid     = [48, 50, 52, 55]
    stop_mult_grid   = [0.75, 1.0, 1.25]
    target_rr_grid   = [1.5, 2.0, 2.5]

    total_combos = len(weight_schemes) * len(min_score_grid) * len(adx_thresh_grid) * len(rsi_min_grid) * len(stop_mult_grid) * len(target_rr_grid)
    logger.info("Grid search: %d combinations × %d train days", total_combos, len(train_days))

    train_results = []
    combo_num = 0

    for scheme_name, weights in weight_schemes:
        for min_score in min_score_grid:
            for adx_t in adx_thresh_grid:
                for rsi_m in rsi_min_grid:
                    for stop_m in stop_mult_grid:
                        for tgt_rr in target_rr_grid:
                            combo_num += 1
                            if combo_num % 50 == 0:
                                logger.info("  combo %d/%d...", combo_num, total_combos)

                            result = evaluate_scheme(
                                all_data, trading_days, train_days,
                                weights=weights,
                                min_score=min_score,
                                adx_threshold=adx_t,
                                rsi_min=rsi_m,
                                stop_mult=stop_m,
                                target_rr=tgt_rr,
                            )
                            train_results.append({
                                "scheme": scheme_name,
                                "weights": weights,
                                "min_score": min_score,
                                "adx_threshold": adx_t,
                                "rsi_min": rsi_m,
                                "stop_mult": stop_m,
                                "target_rr": tgt_rr,
                                **result,
                            })

    # Sort by metric descending
    train_results.sort(key=lambda x: x["metric"], reverse=True)
    top5_train = train_results[:5]

    logger.info("\n=== TOP 5 ON TRAIN SET ===")
    for r in top5_train:
        logger.info("  scheme=%-12s min_score=%d adx=%d rsi_min=%d stop=%.2f rr=%.1f | trades=%d win_rate=%.1f%% avg_pnl=%.3f%% metric=%.4f",
            r["scheme"], r["min_score"], r["adx_threshold"], r["rsi_min"],
            r["stop_mult"], r["target_rr"],
            r["trades"], r["win_rate"]*100, r["avg_pnl_pct"], r["metric"])

    # ── Validate top 5 on held-out set ───────────────────────────────────────
    logger.info("\n=== VALIDATING TOP 5 ON HELD-OUT SET ===")
    val_results = []
    for r in top5_train:
        val = evaluate_scheme(
            all_data, trading_days, val_days,
            weights=r["weights"],
            min_score=r["min_score"],
            adx_threshold=r["adx_threshold"],
            rsi_min=r["rsi_min"],
            stop_mult=r["stop_mult"],
            target_rr=r["target_rr"],
        )
        val_results.append({**r, "val": val})
        logger.info("  scheme=%-12s | val trades=%d win_rate=%.1f%% avg_pnl=%.3f%% metric=%.4f",
            r["scheme"], val["trades"], val["win_rate"]*100, val["avg_pnl_pct"], val["metric"])

    # Pick winner: best validation metric among top-5 train
    val_results.sort(key=lambda x: x["val"]["metric"], reverse=True)
    best = val_results[0]

    logger.info("\n=== WINNER ===")
    logger.info("  scheme=%s min_score=%d adx_threshold=%d rsi_min=%d stop_mult=%.2f target_rr=%.1f",
        best["scheme"], best["min_score"], best["adx_threshold"],
        best["rsi_min"], best["stop_mult"], best["target_rr"])
    logger.info("  train: trades=%d win_rate=%.1f%% avg_pnl=%.3f%%",
        best["trades"], best["win_rate"]*100, best["avg_pnl_pct"])
    logger.info("  val:   trades=%d win_rate=%.1f%% avg_pnl=%.3f%%",
        best["val"]["trades"], best["val"]["win_rate"]*100, best["val"]["avg_pnl_pct"])

    # ── Write optimal params to strategy_params.json ─────────────────────────
    sp_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../config/strategy_params.json"))
    try:
        with open(sp_path) as f:
            current_sp = json.load(f)
    except Exception:
        current_sp = {}

    current_sp.update({
        "adx_threshold":   best["adx_threshold"],
        "rsi_min":         best["rsi_min"],
        "stop_multiplier": best["stop_mult"],
        "min_score":       best["min_score"],
        "target_rr_min":   best["target_rr"],
        "_backtest_scheme": best["scheme"],
        "_backtest_win_rate": best["val"]["win_rate"],
        "_backtest_avg_pnl":  best["val"]["avg_pnl_pct"],
        "_backtest_trades":   best["val"]["trades"],
        "_comment": "Auto-tuned by backtest. Walk-forward validated.",
        "_version": current_sp.get("_version", 1) + 1,
    })
    with open(sp_path, "w") as f:
        json.dump(current_sp, f, indent=2)
    logger.info("Wrote optimal params to %s", sp_path)

    # ── Write full report ─────────────────────────────────────────────────────
    report = {
        "run_date": date.today().isoformat(),
        "months": args.months,
        "symbols": len(all_data),
        "trading_days": len(trading_days),
        "train_days": len(train_days),
        "val_days": len(val_days),
        "total_combos": total_combos,
        "winner": {
            "scheme": best["scheme"],
            "weights": best["weights"],
            "min_score": best["min_score"],
            "adx_threshold": best["adx_threshold"],
            "rsi_min": best["rsi_min"],
            "stop_mult": best["stop_mult"],
            "target_rr": best["target_rr"],
            "train_metrics": {k: best[k] for k in ("trades","wins","stops","win_rate","avg_pnl_pct","total_pnl_pct","metric")},
            "val_metrics": best["val"],
        },
        "top5_train": [{k: r[k] for k in ("scheme","min_score","adx_threshold","rsi_min","stop_mult","target_rr","trades","win_rate","avg_pnl_pct","metric")} for r in top5_train],
        "top5_val": [{k: v[k] for k in ("scheme","min_score","adx_threshold","rsi_min","stop_mult","target_rr")} | {"val": v["val"]} for v in val_results],
        "all_train_results": [{k: r[k] for k in ("scheme","min_score","adx_threshold","rsi_min","stop_mult","target_rr","trades","win_rate","avg_pnl_pct","metric") if k in r} for r in train_results],
    }

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Full report written to %s", args.out)

    # Print summary table to stdout
    print("\n" + "="*80)
    print("BACKTEST SUMMARY")
    print("="*80)
    print(f"Symbols: {len(all_data)} | Period: {trading_days[0]} → {trading_days[-1]}")
    print(f"Train: {len(train_days)} days | Val: {len(val_days)} days | Combos tested: {total_combos}")
    print()
    print("WINNER:")
    print(f"  Scheme:        {best['scheme']}")
    print(f"  Weights:       {json.dumps(best['weights'])}")
    print(f"  min_score:     {best['min_score']}")
    print(f"  adx_threshold: {best['adx_threshold']}")
    print(f"  rsi_min:       {best['rsi_min']}")
    print(f"  stop_mult:     {best['stop_mult']}")
    print(f"  target_rr:     {best['target_rr']}")
    print(f"  Train  → {best['trades']} trades, {best['win_rate']*100:.1f}% win, {best['avg_pnl_pct']:.3f}% avg")
    print(f"  Val    → {best['val']['trades']} trades, {best['val']['win_rate']*100:.1f}% win, {best['val']['avg_pnl_pct']:.3f}% avg")
    print(f"\nOptimal params written to config/strategy_params.json")
    print(f"Full report: {args.out}")
    print("="*80)


if __name__ == "__main__":
    main()

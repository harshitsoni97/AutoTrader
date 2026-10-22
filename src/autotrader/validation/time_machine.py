"""Time Machine — pre-trade expectancy for a SPECIFIC setup on a SPECIFIC stock.

Borrowed concept (day-trader-skill), rebuilt market-agnostic and pure. This is NOT a
historical strategy backtest (that answers "did this strategy work over 130 days?").
It answers a different question, asked at the moment of a trade:

    "Given how THIS stock has been moving recently, if I take THIS setup
     (entry/stop/target, held up to N bars), what's the distribution of outcomes?"

Two independent estimates, combined into one report:

  1. monte_carlo — resample the stock's recent per-bar returns to build many synthetic
     forward price paths from the entry, simulate the trade on each (stop / target /
     time-exit), and summarize the R-multiple distribution. Close-path based: a bar
     counts as a stop/target hit when its CLOSE crosses the level (conservative — it
     does not peek at intrabar high/low, which a single close series can't give).

  2. historical_replay — scan the actual recent bars for past bars from which the SAME
     entry->stop->target geometry (scaled to price) would have been taken, and record
     what actually happened next. Small-sample, but real.

Pure and data-source-agnostic: callers pass bars (India: Upstox; UK: Yahoo .L). No
network, no broker. LONG-only (both our systems are long-only); R = (exit-entry)/risk
where risk = entry-stop.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass
class SetupSpec:
    """A concrete long setup to evaluate. entry > stop, target > entry."""
    entry: float
    stop: float
    target: float
    horizon_bars: int = 75     # max bars held before a time-exit (e.g. ~ a session of 5-min bars)

    def risk(self) -> float:
        return self.entry - self.stop

    def is_valid(self) -> bool:
        return self.entry > self.stop > 0 and self.target > self.entry and self.horizon_bars > 0


def _bar_log_returns(bars: list[dict]) -> list[float]:
    """Per-bar log returns from a list of OHLCV dicts (needs 'close'). Skips bad rows."""
    closes = [float(b["close"]) for b in bars if b.get("close") and float(b["close"]) > 0]
    out = []
    for i in range(1, len(closes)):
        out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _simulate_path(entry: float, stop: float, target: float, horizon: int,
                   returns: list[float], rng: random.Random) -> float:
    """One synthetic forward path from entry; return the realized R-multiple.

    Walk up to `horizon` bars, each bar applying a bootstrapped log return to the price.
    Exit on the first CLOSE that is <= stop (stop) or >= target (target); else time-exit
    at the final close. R = (exit - entry) / (entry - stop).
    """
    risk = entry - stop
    price = entry
    exit_price = entry
    for _ in range(horizon):
        price *= math.exp(rng.choice(returns))
        if price <= stop:
            exit_price = stop
            break
        if price >= target:
            exit_price = target
            break
        exit_price = price
    return (exit_price - entry) / risk


def _summarize(rs: list[float]) -> dict:
    if not rs:
        return {}
    s = sorted(rs)
    n = len(s)

    def pct(p: float) -> float:
        if n == 1:
            return s[0]
        idx = p * (n - 1)
        lo, hi = int(math.floor(idx)), int(math.ceil(idx))
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    wins = [r for r in rs if r > 0]
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "expectancy_r": round(sum(rs) / n, 4),       # mean R — the number that matters
        "median_r": round(pct(0.5), 4),
        "p10_r": round(pct(0.10), 4),                # downside: a bad-but-plausible outcome
        "p90_r": round(pct(0.90), 4),                # upside
    }


def monte_carlo(bars: list[dict], setup: SetupSpec, n_sims: int = 10000,
                seed: int | None = None) -> dict:
    """Monte-Carlo expectancy from resampled recent per-bar returns. {} if not enough data."""
    if not setup.is_valid():
        return {}
    returns = _bar_log_returns(bars)
    if len(returns) < 10:
        return {}
    rng = random.Random(seed)
    rs = [_simulate_path(setup.entry, setup.stop, setup.target, setup.horizon_bars, returns, rng)
          for _ in range(max(1, n_sims))]
    out = _summarize(rs)
    out["method"] = "monte_carlo"
    return out


def historical_replay(bars: list[dict], setup: SetupSpec) -> dict:
    """Replay the SAME geometry (scaled to each past bar's price) over the real bars.

    For each past bar i (that leaves `horizon` bars of future), anchor entry at that
    bar's close, place stop/target at the SAME proportional distances as `setup`, and
    walk the ACTUAL subsequent closes to a stop/target/time exit. Returns the R
    distribution of those real forward windows. {} if too few windows.
    """
    if not setup.is_valid():
        return {}
    closes = [float(b["close"]) for b in bars if b.get("close") and float(b["close"]) > 0]
    h = setup.horizon_bars
    if len(closes) < h + 5:
        return {}
    stop_frac = setup.stop / setup.entry       # < 1
    tgt_frac = setup.target / setup.entry       # > 1
    rs: list[float] = []
    for i in range(len(closes) - h):
        entry = closes[i]
        stop = entry * stop_frac
        target = entry * tgt_frac
        risk = entry - stop
        if risk <= 0:
            continue
        exit_price = entry
        for j in range(i + 1, i + 1 + h):
            c = closes[j]
            if c <= stop:
                exit_price = stop
                break
            if c >= target:
                exit_price = target
                break
            exit_price = c
        rs.append((exit_price - entry) / risk)
    out = _summarize(rs)
    out["method"] = "historical_replay"
    return out


def time_machine(bars: list[dict], setup: SetupSpec, n_sims: int = 10000,
                 seed: int | None = None) -> dict:
    """Combined pre-trade report: monte_carlo + historical_replay + a one-line verdict.

    `verdict` is advisory only (expectancy-based): 'favorable' (>0.1R), 'marginal'
    (0..0.1R), 'unfavorable' (<0). Callers decide whether to gate on it.
    """
    mc = monte_carlo(bars, setup, n_sims=n_sims, seed=seed)
    hist = historical_replay(bars, setup)
    exp = mc.get("expectancy_r")
    if exp is None:
        verdict = "insufficient_data"
    elif exp > 0.1:
        verdict = "favorable"
    elif exp >= 0:
        verdict = "marginal"
    else:
        verdict = "unfavorable"
    return {
        "setup": {"entry": setup.entry, "stop": setup.stop, "target": setup.target,
                  "horizon_bars": setup.horizon_bars, "rr": round((setup.target - setup.entry) / setup.risk(), 2)
                  if setup.risk() > 0 else None},
        "monte_carlo": mc,
        "historical_replay": hist,
        "verdict": verdict,
    }


def summarize_line(report: dict) -> str:
    """One-line human string for the Slack proposal / journal, e.g.
    'TimeMachine: MC 58% win, exp +0.21R (p10 -1.00/p90 +1.00) | hist 61% n=40 | favorable'
    """
    mc = report.get("monte_carlo", {}) or {}
    hist = report.get("historical_replay", {}) or {}
    parts = []
    if mc:
        parts.append(f"MC {mc['win_rate']*100:.0f}% win, exp {mc['expectancy_r']:+.2f}R "
                     f"(p10 {mc['p10_r']:+.2f}/p90 {mc['p90_r']:+.2f})")
    if hist:
        parts.append(f"hist {hist['win_rate']*100:.0f}% n={hist['n']}")
    parts.append(report.get("verdict", "n/a"))
    return "TimeMachine: " + " | ".join(parts)

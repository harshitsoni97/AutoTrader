"""Opening-Range-Breakout (ORB) signal — the ONE validated intraday edge.

Backtest evidence (6-param robustness grid, 10bps costs, out-of-sample):
significant and positive in every combo, beating a *negative* all-day baseline —
i.e. real alpha, not market drift. Best config OR=15m, vol_mult>=1.5, wide stop.

This module is the single source of truth for the signal — identical logic to
scripts/backtest_orb.py so live == backtest. It is PURE (no I/O): the caller
passes today's intraday candles-so-far and gets a fresh-breakout signal or None.
"""

from __future__ import annotations


def _sorted(candles: list[dict]) -> list[dict]:
    return sorted(candles, key=lambda c: c.get("timestamp", ""))


def opening_range(candles: list[dict], or_min: int, interval: int) -> dict | None:
    """High/low/avg-volume of the first `or_min` minutes. None if too few bars."""
    n_or = max(1, or_min // interval)
    cs = _sorted(candles)
    if len(cs) < n_or:
        return None
    bars = cs[:n_or]
    or_high = max(b["high"] for b in bars)
    or_low = min(b["low"] for b in bars)
    if or_high <= or_low:
        return None
    avg_vol = sum(b.get("volume", 0) for b in bars) / len(bars)
    return {"or_high": or_high, "or_low": or_low, "or_rng": or_high - or_low,
            "or_avg_vol": avg_vol, "n_or": n_or}


def orb_breakout_signal(candles: list[dict], or_min: int = 15, interval: int = 5,
                        vol_mult: float = 1.5, stop_range_mult: float = 2.0) -> dict | None:
    """Fresh long ORB breakout on the MOST RECENT closed bar, else None.

    Fires only when the latest bar is the FIRST post-OR bar to close above OR-high
    with volume > vol_mult × OR-average volume — so a live agent polling each cycle
    acts exactly once, right when the breakout happens. Returns the levels needed to
    build a plan: entry reference (OR high), stop (entry − stop_range_mult × OR range),
    and the OR context.
    """
    cs = _sorted(candles)
    orr = opening_range(cs, or_min, interval)
    if not orr:
        return None
    n_or = orr["n_or"]
    post = cs[n_or:]
    if not post:
        return None

    # First post-OR bar closing above OR-high with volume conviction.
    first_bo = None
    for i, c in enumerate(post):
        if c["close"] > orr["or_high"] and c.get("volume", 0) > vol_mult * max(1.0, orr["or_avg_vol"]):
            first_bo = i
            break
    if first_bo is None:
        return None
    # Only signal when that breakout is the LATEST bar (fresh) — avoids re-entering
    # a name that broke out earlier and that the agent already handled/missed.
    if first_bo != len(post) - 1:
        return None

    entry_ref = orr["or_high"]
    return {
        "signal": "ORB_LONG",
        "or_high": orr["or_high"],
        "or_low": orr["or_low"],
        "or_rng": orr["or_rng"],
        "entry_ref": entry_ref,
        "stop": round(entry_ref - stop_range_mult * orr["or_rng"], 2),
        "stop_range_mult": stop_range_mult,
        "breakout_close": post[first_bo]["close"],
        "breakout_ts": post[first_bo].get("timestamp"),
    }

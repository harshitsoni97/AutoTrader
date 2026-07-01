"""Confidence-scaled position sizing.

A hard confidence cliff (trade full size at 0.75, nothing at 0.74) is crude. This
ramps position size linearly with regime confidence between a hard floor and the
full-size level:

    confidence < confidence_min_trade      → 0.0  (no trade)
    confidence_min_trade .. full_size       → floor_mult .. 1.0 (linear)
    confidence >= confidence_full_size      → 1.0  (full size)

So a moderate-confidence day still trades, but with proportionally less capital —
automatically risking less exactly when conviction is lower.
"""

from __future__ import annotations

from typing import Any


def confidence_size_mult(confidence: float, policy: Any) -> float:
    """Return a size multiplier in [0.0, 1.0] for the given regime confidence."""
    lo = getattr(policy, "confidence_min_trade", 0.65)
    hi = getattr(policy, "confidence_full_size", 0.75)
    floor = getattr(policy, "confidence_floor_size_mult", 0.4)
    if confidence >= hi:
        return 1.0
    if confidence < lo:
        return 0.0
    if hi <= lo:
        return 1.0
    return round(floor + (1.0 - floor) * (confidence - lo) / (hi - lo), 3)

"""Retrieval scoring — recency × relevance × importance.

This is the FinMem / Generative-Agents memory-retrieval formula: a candidate
memory's final score is a weighted blend of how recent it is, how semantically
relevant it is to the query, and how intrinsically important it is.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    # Vectors may be normalised already; clamp for safety
    return max(0.0, min(1.0, dot / (na * nb)))


def recency_decay(last_updated_iso: str, half_life_days: float, now: datetime | None = None) -> float:
    """Exponential time-decay in [0, 1]; 1.0 = just now, 0.5 at one half-life."""
    if half_life_days <= 0:
        return 1.0
    now = now or datetime.now(timezone.utc)
    try:
        ts = datetime.fromisoformat(last_updated_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
    return math.exp(-math.log(2) * age_days / half_life_days)


def composite_score(
    recency: float,
    relevance: float,
    importance: float,
    w_recency: float = 0.34,
    w_relevance: float = 0.33,
    w_importance: float = 0.33,
) -> float:
    """Weighted blend; weights are normalised so they always sum to 1."""
    total = w_recency + w_relevance + w_importance
    if total == 0:
        return 0.0
    return (w_recency * recency + w_relevance * relevance + w_importance * importance) / total

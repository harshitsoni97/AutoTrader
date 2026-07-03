"""Catalyst freshness memory — decay stale catalysts.

A catalyst (buyback, bulk/block deal) is a one-time event. Its intraday punch
fades over days, but the corporate-actions feed keeps returning it, so the scorer
credits it full points every day it stays visible — which is why the same name
(e.g. a stock with a two-week-old buyback) keeps topping the list.

We record when each (symbol, catalyst_type) was FIRST seen and decay its score by
age, so a fresh catalyst gets full credit and a stale one contributes almost
nothing. Uses the session run_date, not the wall clock.

Store: reports/catalyst_seen.json  { "SYMBOL:type": "YYYY-MM-DD" }
"""

from __future__ import annotations

import json
import os
from datetime import date

import structlog

logger = structlog.get_logger()

_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../reports/catalyst_seen.json")
)

# Score halves every HALF_LIFE_DAYS calendar days; never below DECAY_FLOOR.
# A buyback's intraday edge is largely gone within ~a week → half-life 3 days.
HALF_LIFE_DAYS = 3.0
DECAY_FLOOR = 0.10


def _load() -> dict:
    try:
        if os.path.exists(_PATH):
            with open(_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save(store: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)
    except Exception as exc:
        logger.warning("catalyst_memory_save_failed", error=str(exc))


def _age_days(first_seen: str, run_date: str) -> int:
    try:
        return max(0, (date.fromisoformat(run_date) - date.fromisoformat(first_seen)).days)
    except Exception:
        return 0


def _decay(age_days: int) -> float:
    if age_days <= 0:
        return 1.0
    return max(DECAY_FLOOR, round(0.5 ** (age_days / HALF_LIFE_DAYS), 3))


def apply_decay(catalysts: list[dict], run_date: str | None) -> list[dict]:
    """Decay each catalyst's score by how long ago it was first seen.

    Mutates and returns the list. First sighting = full score (records the date);
    thereafter the score is multiplied by an age-based decay factor. Keeps the
    pre-decay value as `catalyst_score_raw` for transparency.
    """
    if not catalysts:
        return catalysts
    run_date = run_date or date.today().isoformat()
    store = _load()
    changed = False

    for c in catalysts:
        sym = c.get("symbol")
        ctype = c.get("catalyst_type", "unknown")
        if not sym:
            continue
        key = f"{sym}:{ctype}"
        first_seen = store.get(key)
        if not first_seen:
            store[key] = run_date
            first_seen = run_date
            changed = True
        age = _age_days(first_seen, run_date)
        factor = _decay(age)
        raw = c.get("catalyst_score", 0)
        c["catalyst_score_raw"] = raw
        c["catalyst_age_days"] = age
        c["catalyst_decay"] = factor
        c["catalyst_score"] = round(raw * factor, 1)
        if factor < 1.0:
            logger.info("catalyst_decayed", symbol=sym, type=ctype, age_days=age,
                        decay=factor, raw=raw, decayed=c["catalyst_score"])

    if changed:
        _save(store)
    return catalysts

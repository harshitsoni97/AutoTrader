"""Pending-repricing store.

When post-market runs before Upstox has published the day's candles, the trades
can't be priced. Instead of reporting ₹0, we record that run_date here; the next
pre-market run re-prices it (candles are guaranteed available by then) and sends
the final P&L. Removes any dependency on exactly when Upstox settles data.

Store: reports/pending_reprice.json  → ["2026-07-06", ...]
"""

from __future__ import annotations

import json
import os

import structlog

logger = structlog.get_logger()

_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../reports/pending_reprice.json")
)


def _load() -> list[str]:
    try:
        if os.path.exists(_PATH):
            with open(_PATH) as f:
                data = json.load(f)
            return [d for d in data if isinstance(d, str)]
    except Exception:
        pass
    return []


def _save(dates: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "w") as f:
            json.dump(sorted(set(dates)), f, indent=2)
    except Exception as exc:
        logger.warning("pending_reprice_save_failed", error=str(exc))


def add(run_date: str) -> None:
    if not run_date:
        return
    dates = _load()
    if run_date not in dates:
        dates.append(run_date)
        _save(dates)
        logger.info("pending_reprice_added", run_date=run_date)


def list_pending() -> list[str]:
    return _load()


def remove(run_date: str) -> None:
    dates = [d for d in _load() if d != run_date]
    _save(dates)
    logger.info("pending_reprice_removed", run_date=run_date)

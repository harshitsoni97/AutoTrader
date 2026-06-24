"""Session state persistence — saves pre-market output and loads it post-market.

Stores a minimal JSON snapshot under reports/<date>_session.json so
post-market can compute dry-run assumed P&L without re-running the pipeline.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_REPORTS_DIR = Path(__file__).parent.parent.parent.parent / "reports"


def _session_path(run_date: str) -> Path:
    return _REPORTS_DIR / f"{run_date}_session.json"


# Keys we persist — everything needed for post-market P&L and learning
_PERSIST_KEYS = [
    "run_date", "session_type", "market_regime", "market_confidence",
    "india_vix", "options_pcr", "options_signal", "top_sectors",
    "scored_opportunities", "trade_plan", "trade_plans",
    "orders", "positions", "daily_trades_taken", "daily_pnl",
    "consecutive_losses", "dry_run", "competitor_results",
]


def save_session(state: dict[str, Any]) -> Path:
    """Persist key state fields to disk after pre-market run."""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_date = state.get("run_date", date.today().isoformat())
    path = _session_path(run_date)
    snapshot = {k: state.get(k) for k in _PERSIST_KEYS if k in state}
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    logger.info("session_saved", path=str(path))
    return path


def load_session(run_date: str | None = None) -> dict[str, Any] | None:
    """Load today's (or a specific date's) session snapshot. Returns None if missing."""
    rd = run_date or date.today().isoformat()
    path = _session_path(rd)
    if not path.exists():
        logger.warning("session_not_found", path=str(path))
        return None
    with open(path) as f:
        data = json.load(f)
    logger.info("session_loaded", run_date=rd, trades=len(data.get("orders", [])))
    return data

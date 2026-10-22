"""Daily P&L ledger — a tiny append-only record the strategist reads.

The hybrid strategist needs 'how did the last few days go?' to de-risk after losses.
Post-market appends one row per completed day; the strategist reads the last N. Stored
as reports/pnl_ledger.json (gitignored-friendly, local state). Idempotent per date:
re-writing the same run_date updates its row rather than duplicating it.
"""

from __future__ import annotations

import json
from pathlib import Path

_LEDGER = Path("reports") / "pnl_ledger.json"


def _load() -> list[dict]:
    try:
        return json.loads(_LEDGER.read_text()) or []
    except Exception:
        return []


def record(run_date: str, pnl: float, regime: str = "", trades: int = 0) -> None:
    """Append (or update) the row for run_date. Never raises into the trading path."""
    try:
        rows = [r for r in _load() if r.get("date") != run_date]
        rows.append({"date": run_date, "pnl": round(float(pnl), 2),
                     "regime": regime or "", "trades": int(trades or 0)})
        rows.sort(key=lambda r: r.get("date", ""))
        _LEDGER.parent.mkdir(parents=True, exist_ok=True)
        _LEDGER.write_text(json.dumps(rows, indent=2))
    except Exception:
        pass


def recent_pnls(n: int = 5) -> list[float]:
    """Most-recent-LAST list of the last n daily P&Ls."""
    return [float(r.get("pnl", 0.0)) for r in _load()[-n:]]


def recent_rows(n: int = 5) -> list[dict]:
    return _load()[-n:]

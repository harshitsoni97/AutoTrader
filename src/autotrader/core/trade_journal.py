"""Trade journal — append-only record of each trade's plan vs. realized outcome.

This is the dataset for evaluating (and later RL-tuning) the adaptive target
logic. Each row captures what the plan chose (RR multiple, ATR, levels) and what
actually happened (scenario, realized P&L, EOD price), so we can answer questions
like "is T2 rarely hit in trends → are we too greedy?" before exposing those
breakpoints to the RL search space.

One JSON object per line (.jsonl) in reports/trade_journal.jsonl.
"""

from __future__ import annotations

import json
import os
import structlog

logger = structlog.get_logger()

_JOURNAL_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../../reports/trade_journal.jsonl")
)


def append_outcomes(run_date: str, regime: str, dry_run: bool, outcomes: list[dict]) -> int:
    """Append one journal row per trade outcome. Returns rows written."""
    if not outcomes:
        return 0
    try:
        os.makedirs(os.path.dirname(_JOURNAL_PATH), exist_ok=True)
        written = 0
        with open(_JOURNAL_PATH, "a") as f:
            for o in outcomes:
                entry = float(o.get("entry") or 0)
                t1 = float(o.get("target1") or 0)
                t2 = float(o.get("target2") or 0)
                stop = float(o.get("stop") or 0)
                row = {
                    "run_date": run_date,
                    "regime": regime,
                    "dry_run": dry_run,
                    "symbol": o.get("symbol"),
                    "pattern": o.get("pattern"),
                    "score": o.get("score"),
                    # plan
                    "entry": entry,
                    "stop": stop,
                    "target1": t1,
                    "target2": t2,
                    "target2_rr": o.get("target2_rr"),
                    "atr_used": o.get("atr_used"),
                    # realized
                    "scenario": o.get("scenario"),
                    "eod_price": o.get("eod_price"),
                    "pnl": o.get("pnl"),
                    # derived target distances (% from entry) — handy for analysis
                    "t1_pct": round((t1 - entry) / entry * 100, 3) if entry else None,
                    "t2_pct": round((t2 - entry) / entry * 100, 3) if entry else None,
                    "stop_pct": round((stop - entry) / entry * 100, 3) if entry else None,
                    "t1_hit": o.get("scenario") in ("target1_hit_partial", "target2_hit"),
                    "t2_hit": o.get("scenario") == "target2_hit",
                    "stopped": o.get("scenario") == "stopped_out",
                }
                f.write(json.dumps(row) + "\n")
                written += 1
        logger.info("trade_journal_appended", rows=written, path=_JOURNAL_PATH)
        return written
    except Exception as exc:
        logger.warning("trade_journal_append_failed", error=str(exc))
        return 0

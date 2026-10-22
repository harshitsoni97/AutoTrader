#!/usr/bin/env python3
"""One-time backfill of reports/pnl_ledger.json from the journaled daily history.

Idempotent (record() updates a date in place), so re-running is safe. Run once on OCI:
    PYTHONPATH=src ./venv/bin/python scripts/seed_pnl_ledger.py
After this the hybrid strategist has real recent P&L to reason over immediately.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from autotrader.core.pnl_ledger import record, recent_rows

# (date, pnl, regime, trades) — from docs/run_journal.md daily summaries.
HISTORY = [
    ("2026-07-22",  370.0, "cautiously_bullish", 3),
    ("2026-07-23", -473.0, "bearish",            4),
    ("2026-07-24",   50.0, "bearish",            3),
    ("2026-07-27",  109.0, "range_bound",        3),
    ("2026-07-28",  499.0, "bullish",            4),
    ("2026-07-31", -303.0, "risk_on",            6),
    ("2026-08-03", 1138.0, "risk_on",            5),
    ("2026-08-04", -314.0, "risk_on",            4),
    ("2026-08-07",  428.0, "bullish",            5),
    ("2026-08-10",  181.0, "bullish",            3),
    ("2026-08-11", 1243.0, "bullish",            3),
    ("2026-08-12", -348.0, "range_bound",        3),
    ("2026-08-13",  353.0, "range_bound",        3),
    ("2026-08-14", -946.0, "bullish",            3),
    ("2026-08-17", -484.0, "bearish",            3),
    ("2026-08-18", -622.0, "range_bound",        3),
    ("2026-08-19",   14.0, "bearish",            3),
]

if __name__ == "__main__":
    for d, pnl, regime, trades in HISTORY:
        record(d, pnl, regime, trades)
    rows = recent_rows(len(HISTORY))
    total = sum(r["pnl"] for r in rows)
    print(f"Seeded {len(rows)} rows. Net ₹{total:+.0f}.")
    print("Last 5:", [f'{r["date"][5:]}:{r["pnl"]:+.0f}' for r in rows[-5:]])

# Observations & Open Issues

## ⚠️ CRITICAL CONTEXT: 2026-06-26 — MARKET WAS CLOSED (festival holiday)

This invalidates most P&L conclusions below. With the market closed:
- **EOD == entry == ₹0 P&L is actually CORRECT** — no trading occurred, so
  there was no price movement. `dry_run_pnl` returning ₹0 is the right answer.
- **BUT the system should NOT have traded at all on a holiday.** It placed 3
  dry-run entries (ANANDRATHI, DRREDDY, RELIANCE) on a closed-market day.
  → NOT a missing-guard bug. `SafetyControls.check_holiday()` DOES detect
    holidays (live Upstox list + hardcoded fallback). But `run_pre_market.py`
    lines 52-60 deliberately treats holiday/weekend as a WARNING and
    "runs anyway" (for analysis). On a holiday this produces meaningless
    dry-run trades with ₹0 P&L.
  → DECISION NEEDED: for paper/dry-run, should pre-market SKIP entirely on
    holidays (return early) instead of running anyway? Recommend yes —
    simulating trades with no market data only pollutes the RL/learning data.
- **Compete leaderboard showed +0.28% — this is SUSPICIOUS.** If the market
  was closed, where did a price move come from? Either:
  (a) stale/previous-day candle being compared, or
  (b) the holiday was a partial/special session, or
  (c) compete's hypothetical_monitor fabricated a move from old data.
  → Investigate where compete got +0.28% on a closed day.

## 2026-06-26 run — confirms OCI was running PRE-FIX code

Evidence from Slack messages that day:

1. **Duplicate Daily Summary still appeared** (3:33 AM pre-market + 11:05 AM post-market).
   - My fix (commit `8bfcfe4`) removed `notify_daily_summary()` from `run_pre_market.py`.
   - The 3:33 AM summary proves OCI had NOT pulled that commit yet.
   - ACTION: `git pull origin claude/dazzling-mendel-pa8dtv` on OCI before next run.

2. **dry_run_pnl still shows EOD == entry → ₹0 P&L** for all 3 trades:
   - ANANDRATHI entry ₹1932.5 → EOD ₹1932.5 (₹0)
   - DRREDDY   entry ₹1350.5 → EOD ₹1350.5 (₹0)
   - RELIANCE  entry ₹1318.1 → EOD ₹1318.1 (₹0)
   - My LTP fix (commit `8bfcfe4`) makes `_eod_price()` call `get_ltp()` first.
   - This ₹0 again = OCI running old historical-candle code.

3. **KEY DISCREPANCY — Compete leaderboard got real prices, dry_run_pnl did not:**
   - Compete EOD: ANANDRATHI **P&L +0.28%** (≈ EOD ₹1937.9, NOT ₹1932.5)
   - dry_run_pnl: ANANDRATHI ₹0 (EOD == entry ₹1932.5)
   - Both run in the SAME post-market graph. Compete's `hypothetical_monitor`
     fetches a live/EOD price successfully; `dry_run_pnl._eod_price()` did not.
   - This means even AFTER the LTP fix, verify both code paths use the SAME
     price source. If compete uses `get_ltp()` and works, dry_run_pnl should too.
   - TODO: confirm post-market actually runs AFTER 15:30 IST. The 11:05 AM
     timestamp is suspicious — if it ran mid-session, LTP != closing price.

4. **Timing concern:** post-market fired 11:05 AM (shown TZ). Confirm the cron
   schedule runs post-market strictly after NSE close (15:30 IST), otherwise
   "EOD" price is just the current intraday LTP.

## Verification checklist for next run (after OCI pulls latest)
- [ ] Only ONE Daily Summary (post-market only), no 3:33 AM duplicate
- [ ] dry_run_pnl P&L matches Compete leaderboard % moves (non-zero)
- [ ] Capital: 3 trades total ~₹100k (score-weighted), not ~₹100k each
- [ ] Confirm post-market cron runs after 15:30 IST

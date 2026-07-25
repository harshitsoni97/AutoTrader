# Live Run Journal

Running log of notable live (dry-run) sessions — what fired, what worked, what to fix.
Newest first. This is the human-readable companion to the pick-attribution log.

---

## 2026-07-24 — ORB green on a risk_off/bearish day ✅ (+₹50)

Composite booked NOTHING (`Eligible opportunities: 0` — floor correctly gated the
composite in risk_off 62%). All trades are pure ORB, held-to-close:

| Symbol | Fill → Close | P&L |
|---|---|---|
| ITC | 282.0 → 284.0 | +₹225 |
| NTPC | 349.3 → 347.7 | −₹123 |
| ADANIPORTS | 1776.4 → 1772.1 | −₹51 |

**Day: +₹50, 1W/2L — winner covered both losers.** Notable vs 7/23: same bearish
regime family, opposite outcome. 7/23 died on one big −538 stop; here the small
losers stayed small and ITC carried it. That's the variance we're sampling.

**`max_positions=3` cap fired:** 4 breakouts detected, 3 booked — the 4th was
capped, NOT a book failure (matches ORBConfig.max_positions=3). Composite/ORB
separation is clean: floor gates composite, ORB books independently.

Infra: idempotency guard held — ONE daily summary at 11:05 AM, no triple-send.

**3-day tally: +₹370 / −₹473 / +₹50 = −₹53.** Still tiny sample; keep logging.

---

## 2026-07-23 — ORB day 2: a LOSING day ❌ (expected variance)

Bearish tape — exactly where a long-only breakout strategy bleeds. 4 breakouts
detected → 4 trades booked (100% scan→book conversion; plumbing clean):

| Symbol | Outcome | P&L |
|---|---|---|
| DLF | STOP (full ~1R) | −₹538 |
| ITC | small loss | −₹18 |
| HCLTECH | tiny win | +₹7 |
| INFY | win | +₹76 |

**Day: −₹473, 1W/3L.** DLF broke its OR-high, failed, and reversed straight into
the wide 2×OR stop — the classic false-breakout on a down day. Nothing broke: ORB
is long-only, so a bearish session is its designed weakness, not a bug.

**2-day tally: +₹370 / −₹473 = −₹103** (across ~4W/1 big-L). Far too small a sample
to judge vs the backtest's +0.29%/trade over ~130 days. Keep logging toward ~15–20
trades before a live bootstrap CI. What to watch: how ORB does across regimes — we
now have one bullish-ish (+) and one bearish (−) day, consistent with a long-only
breakout's shape.

---

## 2026-07-22 — ORB's FIRST PROFITABLE TRADING DAY ✅✅

The validated edge booked and made money live for the first time. Regime was only
cautiously_bullish 57% (below the composite floor, so the composite booked nothing) —
these are pure ORB trades, held to close:

| Symbol | Fill → Close | P&L |
|---|---|---|
| POWERGRID | 287.5 → 289.3 | +₹202 |
| DIVISLAB | 7345.1 → 7386.5 | +₹83 |
| NTPC | 349.7 → 350.5 | +₹85 |

**Day: +₹370, 3/3 winners.** The full chain worked: full-quote OR capture → breakout
detection → confidence-floor exemption → EntryAgent books → hold-to-close → post-market
marks to close. From "no edge + can't book" to a profitable ORB day.

Bug fixed same day: the compete hypothetical monitor spammed ~200 duplicate
"AJANTPHARM STOP HIT" messages (every cycle ×3 stacks) because competitor_results
(carrying the stop_hit flag) wasn't persisted across intraday cycles — added it to the
loop's state carry-over.

Note: 1 day / 3 trades is not proof — ORB needs many sessions across regimes. But it's
the first live, profitable run of a backtest-validated edge. Keep logging.

---

## 2026-07-16 — FIRST FULLY-EXECUTING SESSION ✅ (booking validated)

**Milestone:** the `live_ltp` key-mismatch fix is validated end-to-end. For the first
time the plan→book-at-live→monitor→exit→reentry chain executed real (dry-run) trades.
Regime risk_on 93%; 3 pre-market plans (DIVISLAB, GODREJPROP, HCLTECH); 6 entries over
the day (hit `max_daily_trades=6`).

**Trades (realized):**
| Symbol | Entry | Exit | Outcome | P&L |
|---|---|---|---|---|
| DIVISLAB | 7294.24 | T1 7341 (2) + T2 7382 (2) | win, scaled | +₹254.32 |
| HCLTECH | 1168.00 (reentry) | T2 1198.6 | win | +₹360.00 |
| GODREJPROP | 2094.94 | STOP 2080.1 | loss | −₹238.20 |
| OBEROIRLTY | 1878.69 (reentry) | STOP 1867.1 | loss | −₹325.52 |
| SUNPHARMA | 1950.22 (reentry) | open at last msg | ? | ? |
| TCS | 2203.34 (reentry) | open at last msg | ? | ? |

Realized on closed: **≈ +₹50.6** (2 win / 2 loss), plus SUNPHARMA + TCS open — need the
daily summary for the final number.

**What worked:**
- Entry books at the live price; T1 partial + T2 runner + stops all fired correctly.
- Reentry redeployed freed capital (HCLTECH win came via a reentry). Intraday hunt active.
- DIVISLAB scaled out cleanly (T1 half, T2 half) — the plan→book→scale flow is sound.

**Issues to fix (ranked):**
1. **Near-stop entry — GODREJPROP.** Booked at 2094.94 with a stop at 2090.06 — only
   ₹4.88 (≈34% of the intended risk) of room left; it had already retraced ~66% from the
   plan entry (2104.40) toward the stop *before we booked*. Stopped out 15 min later
   (−₹238). Booking a long that has already collapsed toward its stop is a broken setup.
   → FIX: skip the entry when the live price has retraced > ~50% of plan-entry→stop.
2. **Entry timing ~10:30 IST (5:00 UTC)** — ~1h15 after the open. Investigate why entries
   booked so late (guards skipping early gap-ups, then booking on the pullback?). May be
   missing the cleaner early-session entry.
3. **High churn — 6 entries, hit the daily cap, ~breakeven.** Consistent with the intraday
   backtest (lots of ~coin-flip trades; reentry Δ vs naive was negative). Open question:
   does aggressive reentry/hunt redeployment add edge or just churn slippage? Validate.

**Still overbought-leaning selection:** DIVISLAB RSI 72 top pick again (won this time
+254). The RSI dampener (RSI≥75) doesn't touch 72 — deliberately (tail-only). Watch.

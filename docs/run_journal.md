# Live Run Journal

Running log of notable live (dry-run) sessions — what fired, what worked, what to fix.
Newest first. This is the human-readable companion to the pick-attribution log.

---

## 2026-08-04 — red −₹314 on risk_on 73%; DIVISLAB RSI-89 stopped ❌

4 trades: BSOFT +127 (IT), LT −331, IRCTC −97 (Midcap), DIVISLAB −195 (Pharma, reentry
STOPPED). All 3 compete LLMs screamed DIVISLAB **RSI 89** "severely overbought" — and
it stopped both hypothetically (−2.28%) AND when we booked it live via reentry (−195).
The deterministic composite correctly avoided DIVISLAB as top pick (chose BSOFT), but
the **reentry path booked DIVISLAB anyway** at RSI 89 → −195.
- **The overbought penalty (RSI≥85→12) should have hammered DIVISLAB's score — yet the
  reentry booked it. ACTION: does the reentry/hunt path apply the overbought penalty +
  sector gate, or bypass them?** Losses here were stock-specific (sectors weren't red at
  book, so the gate correctly didn't fire) — this one is an OVERBOUGHT-discipline gap on
  the reentry path, not a sector-gate miss.

## 2026-08-03 — BEST DAY +₹1,138 ✅✅✅ (risk_on 100%)

5 trades: HCLTECH +335 (T2, IT), AXISBANK +482 (Banking), WIPRO +472 (T2 reentry),
SBIN +156 (Banking), ETERNAL **−466 (Midcap, STOPPED)**. A genuine broad risk-on day
and the book rode it — Banking names (AXIS/SBIN) + IT (HCLTECH) all paid.
- **ETERNAL (Midcap) stopped AGAIN — 3rd Midcap wound** (7/31 −342, 8/3 −466).
  CONFIRMED: this run was BEFORE the OCI pull, so the Midcap key was still returning
  **None** → the gate FAILED OPEN on Midcap and let ETERNAL book. Once the 8/02 Midcap
  fix is pulled, the gate will actually see Midcap and this is the loss it should catch.
  **ACTION next session: verify `sector_intraday_move('Midcap')` returns a number.**
- Note the WIN side is Banking-led (AXIS/SBIN), NOT the pre-market "top sectors:
  IT/Pharma/Midcap". The trailing sector rank was wrong again; we won on names the
  intraday hunt/ORB surfaced, not the stale composite ranking.

**8-day tally: +₹370/−473/+50/+109/+499/−303/+1138/−314 = +₹1,076, 32 trades.**

---

## 2026-08-02 — FIX: intraday sector gate (composite plans) 🔧

Root-caused the 7/31 loss to stale-sector momentum (audit: `sector_rotation.py`
ranks sectors on 0.5×5d + 0.3×1d + 0.2×vol — all TRAILING daily bars; no same-day
data; no adv/decl breadth anywhere; regime doesn't even see sectors). Composite
plans are built pre-market on yesterday's leader, then booked blind at the open.

**Gate (config `sector_gate`, default OFF):** at book time the EntryAgent reads the
stock's sector INDEX same-day open→last move (`sector_intraday_move`, full-quote
endpoint) and SKIPS a composite long if the sector is below `min_sector_pct` (-0.4%).
ORB exempt. Fail-open (unavailable read never blocks). Enable via
`SECTOR_GATE_ENABLED=true` or config/sector_gate.yaml.

**7/31 replay with gate ON:** WIPRO (IT -1.8%) SKIPPED (saved -215), ETERNAL
(Midcap -0.6%) SKIPPED (saved -342), SUNPHARMA (Pharma +0.5%) booked (kept +181).
Composite side -376 → +181; ORB unchanged. The day's -303 would've been ~+162.

Next: enable on OCI, watch that it doesn't over-gate on choppy sectors; then decide
whether to also cut the 0.17 trailing sector-score weight / add adv-decl breadth.

---

## 2026-07-31 — LOST −₹303 on a +0.7% broad UP day ❌ (stale-sector flaw exposed)

Regime risk_on 100% — and this time the regime was RIGHT: Nifty **+0.7%** (24,317),
breadth **POSITIVE 1,497 adv vs 835 decl** (~2:1 up). A day where buying almost
anything worked. **We lost −₹303.** Why: our selection concentrated in the WORST
sector.

| Symbol | Source | Result | P&L |
|---|---|---|---|
| SUNPHARMA | composite | T2 hit | +₹181 |
| ONGC | ORB | flat | +₹24 |
| ADANIPORTS | ORB | flat | −₹15 |
| TATASTEEL | ORB | flat | −₹28 |
| WIPRO (IT) | **composite #1 (85.2)** | STOPPED | **−₹215** |
| ETERNAL | composite | STOPPED | **−₹342** |

**Day: −₹303, 2W/4L on a day the market rose 0.7% with 2:1 breadth.**

### THE FLAW — stale-sector momentum (7/28 vs 7/31 is the proof)
Actual leaders 7/31 were **Financials + Autos**; **IT was the WORST** sector (Infosys
−3.6%, HCLTECH/TCS/TechM all down). Our #1 pick was **WIPRO (IT)**. Pre-market ranked
"Top sectors: IT, Pharma, Midcap" — IT top — because IT LED the day before (7/28,
+3.2%). The scorer ranks sectors on TRAILING strength and walked into the rotation.

| Date | Regime call | Reality | Top pick | IT that day | Pick result |
|---|---|---|---|---|---|
| 7/28 | bullish (WRONG) | flat, −breadth | HCLTECH (IT) | **led +3.2%** | won +3.15% |
| 7/31 | risk_on (RIGHT) | +0.7%, +breadth | WIPRO (IT) | **worst −3.6%** | STOPPED −2.5% |

Composite picked IT BOTH days. It's momentum-chasing on yesterday's sector leader with
no rotation/breadth awareness. On 7/31 that meant longing the one sector being sold on
an up day — losing money the broad tape was handing out for free.
**ACTION (code-diagnosable now): does Layer-1/scoring rank sectors on trailing
strength? Feed it (a) market breadth (adv/decl) and (b) SAME-DAY sector rotation, not
prior-day momentum. This is likely the same root cause as the 7/28 regime miss.**

Reentry churn also reappeared: WIPRO was RE-booked live at 7:21 (DRY-RE) at exactly the
plan entry right after SUNPHARMA T1, then stopped 5 min later (−215) — redeploying
freed capital into a losing IT name. Same churn flagged 7/16.

**6-day tally: +₹370 / −₹473 / +₹50 / +₹109 / +₹499 / −₹303 = +₹252, 23 trades.**

---

## 2026-07-28 — best day (+₹499); composite ENGAGED on bullish 91% ✅✅

First day the composite cleared the floor and booked — regime bullish **91%** (VIX
12.7, PCR 1.13), `Eligible opportunities: 5`, 2 composite plans (HCLTECH, BAJAJ-AUTO).

| Symbol | Source | Fill → Close | P&L |
|---|---|---|---|
| BAJAJ-AUTO | composite plan | 11198.7 → T2 11309 | **+₹418 (realized T2)** |
| DLF | ORB | 657.4 → 663.5 | +₹167 |
| GODREJPROP | ORB | 2132.0 → 2144.4 | +₹37 |
| INFY | ORB | 1111.9 → 1105.0 | −₹124 |

**Day: +₹499, 3W/1L.** BAJAJ-AUTO ran to T2 and realized +418 — first composite-plan
winner to actually close a target live. ORB names added +80 net on top.

### Notes to self (operator said "make a note")
1. **We MISSED our own top pick.** HCLTECH was the #1 composite score (74.6) AND the
   unanimous compete pick — and it hit TARGET 1 **+3.15%** (1295.90→1336.70) in the
   hypothetical monitor. But there is NO live HCLTECH entry notification → we did not
   book the biggest winner on the board. Plan stop was ₹1289.12 vs entry ₹1295.90 —
   only ₹6.78 risk (very tight). Likely the entry gapped up past the plan and the
   near-stop/too-far-from-plan guard skipped it, OR it never pulled back to the entry.
   **ACTION: check logs/intraday.log for HCLTECH on 7/28 — why no book?** If the guard
   is rejecting clean gap-up-and-go winners, that's a real cost (the guard was built to
   avoid GODREJPROP-style collapses, but it may be too aggressive on strong opens).
2. **Instrument map missing `MM`** — pre-market warned it's not in
   config/upstox_instruments.json → gets ₹0 P&L. **ACTION: run
   `python3 scripts/update_instruments.py` on OCI to refresh (currently 2412 symbols).**
3. **Sample now mixes composite + ORB.** Prior days were pure ORB (composite gated out
   by low-confidence regimes). Today's high-confidence bullish let composite book, so
   the running tally is no longer ORB-only. Keep the ORB-only vs composite P&L separable
   when we run the bootstrap CI — don't pool them.

### ACTUAL TAPE 7/28 (checked post-close — regime was WRONG)
Nifty **−0.04%** (−10.6 → 23,985), Sensex −0.09% — essentially FLAT. Breadth
NEGATIVE: 1,539 adv vs **2,543 decl** (3:2 down). The ONLY strength was Nifty IT
**+3.2%** (HCLTECH/TCS/TechM led). So:
- Regime said "bullish 91%" → index flat + negative breadth. **Layer-1 over-called
  bullishness — 2nd miss in 2 days** (7/27 called bearish, was bullish). Hypothesis:
  low VIX (12.7) + narrow IT-sector strength is being read as broad-market bullish.
  Regime is tracking a SECTOR, not the index/breadth. Worth a Layer-1 look.
- The HCLTECH miss is WORSE than it looked: it was our top pick AND in the single
  leading sector (IT +3.2%) AND rallied +3.15% — correct stock-selection on a
  −breadth day, and the entry guard didn't book it.
- +₹499 long-only on a 3:2-decliners tape = our names landed right, partly luck
  given the regime miss. Don't over-credit the composite for one flat-tape day.

**5-day tally: +₹370 / −₹473 / +₹50 / +₹109 / +₹499 = +₹555, 17 trades.**

---

## 2026-07-27 — ORB green (+₹109); winner ran, losers cut small ✅

Composite again booked NOTHING (`Eligible opportunities: 0`). Pure ORB, held-to-close:

| Symbol | Fill → Close | P&L |
|---|---|---|
| SUNPHARMA | 1951.8 → 1973.0 | +₹297 |
| HCLTECH | 1297.4 → 1294.9 | −₹37 |
| BHARTIARTL | 1915.6 → 1904.0 | −₹151 |

**Day: +₹109, 1W/2L.** Textbook ORB payoff: one clean trend (SUNPHARMA +297) paid
for two small false-breakout fades. You don't win the count, you win the magnitude.

**Operator note — "2 negative stocks in a bullish market."** Correct observation, and
it's the core ORB lesson: ORB is a PER-STOCK breakout bet, not an index-direction bet.
A green index tilts follow-through odds but doesn't stop individual names from breaking
their OR-high and fading. That's WHY the wide stop + hold-the-winner shape matters:
HCLTECH/BHARTIARTL faded but stayed small; SUNPHARMA trended and carried the day.

**Regime-detection MISS (no cost, but real):** MarketRegime called bearish 66%
pre-market / range_bound by close, but the tape was bullish per the operator. Cost
nothing today (composite floor-gated → 0 books; ORB is regime-agnostic), but it's a
genuine Layer-1 error worth watching — if we ever regime-gate ORB, this would bite.

**4-day tally: +₹370 / −₹473 / +₹50 / +₹109 = +₹56 (net positive), 13 trades.**

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

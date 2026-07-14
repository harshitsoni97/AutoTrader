# Bootstrap Prompt — AutoTrader-FX (24h Forex Algo, LangGraph Multi-Agent)

> Paste this whole document as the opening prompt for a fresh project. It is written
> to a coding agent. It encodes the architecture, the hard-won bug lessons, and the
> validation discipline from the sibling NSE-equity project (AutoTrader), adapted to
> spot forex. Build it as a **separate repository** — forex is different enough
> (24×5 market, pairs, leverage, macro-driven, different data/brokers) that sharing
> a codebase with the equity bot would couple two things that should evolve apart.
> Reuse *patterns and core libs*, not the equity trading graph.

---

## 0. Mission

Build a paper-first, LangGraph multi-agent intraday/swing **spot forex** trading
system for major/minor currency pairs. It ingests market + macro data, classifies a
regime, scores opportunities across pairs, constructs risk-defined plans, books at
live prices, manages exits, and reports over Slack/Telegram — running continuously
because forex has **no daily close and no PDT limit**. Default to dry-run; live
execution is gated behind explicit opt-in + a mandate (committed pairs, size,
exposure) + a filesystem kill-switch.

---

## 1. What forex changes vs equities (design deltas — read first)

The equity bot assumed a single 6.25h session, one exchange, per-symbol catalysts,
and daily candles as the backbone. Forex breaks all of that:

- **24×5, no close.** Sessions roll: Sydney → Tokyo → London → New York, with the
  **London/NY overlap (12:00–16:00 UTC)** the highest-liquidity, highest-edge window.
  There is no "pre-market/post-market"; instead run a **continuous loop with
  session-aware behavior** (size up in the overlap, stand down in the thin
  Sydney-only hours and the Fri-close / Sun-open gap).
- **Pairs, not tickers.** Trade EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD,
  USD/CHF, NZD/USD (majors) + selected crosses (EUR/GBP, EUR/JPY, GBP/JPY). Price a
  pair, quote in pips, size in units/lots. **Correlation is first-class**: EUR/USD and
  GBP/USD are ~0.9 correlated; USD-legs across pairs stack USD exposure. Portfolio
  heat must net exposure **by currency**, not by pair.
- **Leverage + margin.** Positions are leveraged (e.g. 20–50×). Size by **risk per
  trade as % of equity** (e.g. 0.5–1%), never by notional. Track margin usage and a
  hard max aggregate exposure. Model **swap/rollover** (carry) for positions held
  over 17:00 ET, and triple-swap Wednesday.
- **Macro-driven, not stock-catalyst-driven.** The "catalyst" layer becomes an
  **economic-calendar + rates + risk-sentiment** layer: NFP, CPI, central-bank
  decisions (Fed/ECB/BoE/BoJ), PMIs. **Hard rule: no new entries in the N minutes
  around a high-impact release for the affected currency** (spreads blow out, stops
  slip). Rate differentials drive medium-term bias.
- **Spreads + slippage matter more.** Majors are tight (0.1–1 pip) but crosses and
  off-session spreads widen. Model spread explicitly per pair/session; it's a real
  cost on a scalp.
- **Regimes are trend/range/risk-on-off + USD-strength.** Add a **DXY / USD-index**
  read and a **risk sentiment** read (JPY/CHF strength = risk-off). "Long-favorable"
  is per-pair-directional, not market-wide — you can be long AUD/USD and short
  USD/JPY simultaneously in the same risk-on tape.

---

## 2. Architecture (reuse the equity project's shape)

LangGraph `StateGraph` over a `TypedDict` state with `Annotated[list, operator.add]`
reducers for append-only channels (messages, audit_trail). Layered agents:

- **L1 Regime** — session clock, DXY trend, risk sentiment (JPY/CHF, equity futures,
  VIX/MOVE), rate-differential bias. Emits per-currency-strength + a tradeable-window
  flag. **Feed the LIVE signal, not a frozen one** (see Lesson 3).
- **L2 Structure** — per-pair indicators on the working timeframe(s): EMA(9/21/50),
  RSI, ADX, ATR (in pips), VWAP/session-VWAP, Bollinger, Donchian for breakouts,
  session high/low. Multi-timeframe: bias from H1/H4, entries on M15/M5.
- **L3 Opportunity scoring** — composite over {regime/session, trend alignment,
  momentum, volatility fit, spread cost, macro-proximity penalty, correlation
  penalty}. Direction-aware (long or short per pair). Emit an eligible set + a
  broader watchlist (Lesson 5).
- **L4 Governance** — risk gates: max concurrent positions, max risk %, per-currency
  exposure cap, max daily loss (rolling 24h, not calendar-day), news-blackout gate,
  session gate, kill-switch.
- **L5 Execution** — plan construction (entry/stop/targets in pips, ATR-based),
  book at **live bid/ask + spread + slippage**, re-anchor stop/targets to the actual
  fill (Lesson 2), monitor exits (stop/TP/trailing), redeploy freed margin.
- **L6 Reporting + learning** — notifications, trade journal, attribution by
  strategy/session/pair, and a **walk-forward backtest with bootstrap CIs** (Lesson 4).

Notifications, config, slippage, sizing, snapshot, logging libs port over largely
unchanged.

---

## 3. HARD-WON LESSONS (do not re-learn these the expensive way)

These are real bugs/traps the equity project hit. Bake the fixes in from day one.

1. **Broker/data key-format mismatch will silently zero you out.** The equity bot
   spent *weeks* taking 0 trades because the live-price lookup requested by one key
   format and the API responded keyed by another, so every price came back `None`
   and every entry was skipped — with only a benign "no live price" log. **Mitigation:**
   (a) never match responses by a key you didn't confirm the API echoes; for a
   single-instrument request, use the returned value directly. (b) Add a **startup
   self-test** that fetches a live price for one instrument and *hard-fails* if it's
   `None` during market hours. (c) A "plans made but 0 booked" state must raise a
   loud alert, never pass silently.

2. **Book at the live fill, then RE-ANCHOR stop/targets to it.** Plans are built
   pre-entry off a reference price; the actual fill differs. Carrying the plan's
   levels compresses R:R. Recompute stop/TP as fill ± (ATR-based distances).

3. **Regime/'"current" signals must be LIVE, not frozen.** The equity regime used
   pre-open inputs that never updated intraday, so it stayed "bearish" through a
   day that pivoted green and never armed. For forex, the session/DXY/sentiment read
   must recompute on live data each loop; down-weight stale overnight inputs once the
   active session is underway.

4. **Validate on a walk-forward with BOOTSTRAP SIGNIFICANCE — never tune on
   anecdotes.** We nearly shipped a "+13.38%" edge that a 131-instrument bootstrap
   revealed was a coin-flip (P(mean>0)≈55%, CI straddled 0). Rules: train/validate
   split; report a 95% CI on mean per-trade P&L + P(>0); an edge whose CI straddles 0
   is **not** real. **Guard against look-ahead**: score on data through the *prior*
   bar and trade the *next* — a backtest that scores using the same bar it trades
   will show a fake ~90% win rate. Compare the strategy to a **naive baseline**
   (e.g. hold-through-session) with its own CI — if the machinery doesn't beat naive,
   the machinery is cost, not edge.

5. **Persist a broader watchlist for intraday re-evaluation.** If the pre-filter
   drops everything, keep a ranked pre-eligibility list so the loop can revive names
   when the regime/session improves — otherwise a quiet open locks you out all day.

6. **Timezone/scheduling: the server runs UTC; the market has its own clock.**
   Forex is UTC-native (good), but session boundaries, the daily 17:00 ET rollover,
   triple-swap Wed, and the Fri 21:00 UTC close / Sun 21:00 UTC open are all
   clock-specific. A single wrong offset silently trades the dead zone. Make the
   session clock a tested, first-class module. Prefer one long-lived loop with an
   internal session state machine over cron edges.

7. **Portfolio heat must net by the right unit.** Equities netted by sector; forex
   nets by **currency leg**. Three "different" pairs can be one big USD bet.

8. **Confidence/quality floors cause flat days — measure the tradeoff.** A floor too
   high sits out good moves; too low chases. Make it a tuned parameter, validated,
   not a guess.

9. **Overbought/extension: use the right scale.** RSI-based overbought helped filter
   grind-up names that a VWAP-extension metric missed. But scale stops/targets to the
   **trading timeframe** — daily-ATR levels on an intraday hold barely interact and
   make the exit machinery a no-op.

10. **Don't let one LLM's confident pick override the risk gates.** In compete/advisory
    setups the LLMs repeatedly favored overbought names that lost. Keep LLMs as
    *advisory/ranking*; deterministic governance + validated scoring decide.

---

## 4. Data & broker (spot forex)

- **Market data:** OANDA v20 REST/stream (excellent for algo, practice accounts free),
  or Polygon.io FX, Twelve Data, Dukascopy, TrueFX. Prefer one with a **streaming**
  price feed for live bid/ask and a historical candle endpoint for indicators/backtest.
- **Broker/execution:** OANDA v20 or IG or Interactive Brokers FX. OANDA has the
  cleanest API and a practice environment mirroring live — ideal for paper→live.
- **Economic calendar:** a machine-readable calendar (e.g. a scraped/normalized feed)
  for news-blackout timing and rate events.
- **Token/secret handling (CRITICAL, same discipline as the equity repo):** ALL
  credentials from environment variables only — broker keys, stream tokens, LLM keys,
  notification tokens. Never in committed config/code. Token files gitignored +
  `chmod 600`. If a token needs daily refresh, use a **time-gated local intake
  service** (see the equity repo's `token_intake` pattern) rather than pasting into
  code.

---

## 5. Strategy seeds (validate each before trusting — Lesson 4)

Start simple, add only what beats the naive baseline out-of-sample:
- **London-breakout:** trade the break of the Asian-session range at the London open,
  on majors, with ATR stops and a news-blackout guard.
- **Trend-pullback:** H4 trend + M15 pullback to EMA/VWAP with momentum confirmation.
- **Session-VWAP mean-reversion:** in range regimes, fade extensions from session VWAP
  on low-ADX pairs.
- **Carry-aware bias:** tilt directional bias toward positive-swap pairs when trend and
  rate-differential agree (swing horizon).

Parameter tuning: walk-forward + bootstrap, optimize under the *live* scoring formula
(not a proxy), and never auto-write live params from a run that isn't significant.

---

## 6. Build order

1. Repo skeleton, config, secrets-from-env, notifications, logging, **startup
   price self-test** (Lesson 1).
2. Data adapters (stream + historical) + instrument/session clock module (tested).
3. Indicators (pips-native) + regime (live, session-aware).
4. Scoring (direction-aware) + governance (per-currency heat, news blackout).
5. Execution (book-at-live, re-anchor, monitor, trailing) — **paper only**.
6. Reporting + trade journal + attribution.
7. **Walk-forward backtest with bootstrap CIs + naive baseline + look-ahead guard**
   (build this alongside, not after — it's how you avoid shipping noise).
8. Only then: bounded live execution behind opt-in + mandate + kill-switch.

---

## 7. References worth mining

- **TradingAgents** (arXiv 2412.20138) — LangGraph multi-agent trading; borrow the
  reflection/outcome-memory loop (feed past trade outcomes back into scoring), not the
  expensive per-decision debate.
- **Vibe-Trading** (HKUDS) — its validation rigor (walk-forward, Monte Carlo/bootstrap,
  PIT-safe evaluation) and the Alpha-Zoo factor libraries + IC ranking as a principled
  way to *choose* signal weights instead of hand-tuning.
- **OpenBB** — for macro/rates/DXY/global data if you want a unified provider layer.
- Practitioner indicator research: ADX/ATR for trend-strength-scaled targets;
  Donchian/ORB for session breakouts; session-VWAP for intraday fair value.

---

## 8. Non-negotiables (carry over verbatim)

- Paper/dry-run by default; live behind explicit opt-in + mandate + filesystem
  kill-switch + audit ledger.
- All secrets from env; token files gitignored + `chmod 600`; never commit a token or
  a model identifier.
- Every scoring/strategy change ships with a walk-forward + bootstrap result, or it
  doesn't ship.
- A silent "0 trades" is a bug until proven otherwise — alert on it.

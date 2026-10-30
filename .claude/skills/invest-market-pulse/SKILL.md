---
name: invest-market-pulse
description: >-
  Market and holdings news check for Indian long-term investors. Gathers the
  macro backdrop (Nifty/Sensex level and drawdown, Nifty P/E vs its long-run
  average, FII/DII flows, crude, rupee, RBI repo and stance, gold in INR) and
  the latest quarterly results and news for every holding over ~3% weight,
  then says which planned decisions the news confirms, changes or should
  wait for (e.g. results due next week). Use when the user says "check the
  market", "what's the market condition", "any news on my stocks", "latest
  results", "is now a good time to invest", or before finalising
  /invest-rebalance.
argument-hint: "[symbols]"
---

# invest-market-pulse

The tools in this repo are offline. This skill uses WebSearch / WebFetch
for live context. If those are unavailable, ask the user to paste headlines
instead, and never fill gaps from memory without saying how old it is.

## 1. Macro backdrop (one table, with a date on every number)

| item | where to look |
| --- | --- |
| Nifty 50 / Sensex level, 52-week high/low, YTD | NSE, Moneycontrol, ET Markets |
| Nifty P/E vs ~22 long-run average | NSE index factsheet, tapetide / indexpe trackers |
| FII / DII net flows, month and YTD | NSDL FPI monitor, NSE provisional data |
| Brent crude, USD/INR | any wire report |
| RBI repo rate, stance, next MPC date | RBI press release |
| Gold (24K ₹/g) | IBJA rate, Nippon GoldBees NAV |

Turn it into one line on **deployment pace**:
- P/E above about 24, or a euphoric market → build the core slowly via SIP and keep the satellites small.
- P/E near or below average, with a falling market → keep the plan, and stagger lump sums over 3-6 months.
- Never "wait for the bottom" with the whole amount. Sells that improve quality don't depend on timing.

## 2. Holdings news

For every holding ≥3% weight, and every name the plan adds or exits, search
"<company> Q<n> FY<yy> results" and "<company> share news <month year>". Note:
the latest quarter (revenue/profit growth, margins, asset quality for lenders),
guidance changes, management changes, big deals or debt, the results date if
it's within 3 weeks, and the 52-week range.

Then make a table with the columns **stock | news | effect on the plan**. The effect is one of:
- **confirms**: the news supports the planned action
- **changes**: e.g. an exit becomes a hold because the turnaround shows in the reported numbers. Edit `investor/decisions.yaml`
- **wait**: results are due soon, so act after them

## 3. Write it down

Add the macro and news sections to the review report, list the source URLs,
and say that aggregator figures sometimes disagree. Prices from news are
indicative; the broker's price is the one that fills.

## Rules

- Headlines are not a thesis. One bad quarter changes a decision only if it
  breaks a line in `investor/theses/SYMBOL.md`.
- Never present a forecast or a brokerage target as a fact. Attribute it.
- Education, not advice.

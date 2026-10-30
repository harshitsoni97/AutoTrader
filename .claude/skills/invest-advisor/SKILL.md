---
name: invest-advisor
description: >-
  Long-Term Investment Advisor for Indian equities (NSE/BSE). The entry point
  that runs the whole review: reads a broker holdings export (Zerodha Console,
  Groww, Upstox .xlsx/.csv), diagnoses the portfolio, works out Indian
  capital-gains tax and harvesting, proposes a rebalance into a core (index
  ETFs) + satellite (conviction stocks) structure, and sets up a monthly SIP.
  Use when the user says "review my portfolio", "optimise my holdings",
  "what should I sell", "my portfolio is down", "help me invest long term",
  "rebalance", uploads a holdings file, or asks for a periodic review.
argument-hint: "[path to holdings export]"
---

# invest-advisor

The long-term counterpart to the intraday AutoTrader graphs. It never trades.
It turns a holdings file into a written plan the user executes themselves.

Seven skills, each reading what the last one wrote into `investor/` (git-ignored,
because holdings are personal and this repo may be public):

```
invest-portfolio-doctor -> invest-tax-harvest -> invest-research / invest-ev -> invest-thesis
                                       \-> invest-rebalance (decisions -> trade list + tax + SIP)
```

| skill | job |
| --- | --- |
| `/invest-portfolio-doctor` | allocation, concentration, fragmentation, deep losers, asset mix |
| `/invest-tax-harvest` | STCG/LTCG by holding, loss harvesting, ₹1.25L LTCG exemption |
| `/invest-research` | fundamentals quality score (ROCE, cash conversion, pledge, valuation) |
| `/invest-ev` | bull/base/bear scenarios vs the index hurdle |
| `/invest-thesis` | one-page thesis per holding + the quarterly review |
| `/invest-rebalance` | decisions -> exact sells/buys, tax impact, SIP split |

## The full review, step by step

1. **Get the file.** Copy the export to `investor/holdings-YYYY-MM-DD.xlsx`.
   Ask once for: realised gains/losses already booked this financial year
   (broker tax P&L), monthly SIP capacity, horizon, and whether an emergency
   fund (6 months of expenses) already sits outside the portfolio. If there is
   no emergency fund, that comes before any equity buying.
2. **Diagnose** (`/invest-portfolio-doctor`). Lead with the 3-5 flags that
   matter most, in plain words, with rupee amounts.
3. **Tax picture** (`/invest-tax-harvest`). Which losses are worth booking and
   when, how much LTCG can be booked tax-free.
4. **Sort every holding into one bucket**, writing `investor/decisions.yaml`:
   - *core*: index ETFs (Nifty 50, Next 50, Midcap 150) and gold
   - *keep / add*: the satellites the user can state a thesis for in two lines
   - *trim*: over the single-stock (10%), group (15%) or sector (25%) cap
   - *exit*: duplicates (two names doing the same job: keep the leader),
     token positions under 2% with no plan to build them, thesis broken, and
     positions whose only reason is the price paid (anchoring).
   For anything not obvious, run `/invest-research` and `/invest-ev` first.
   Show the bucket list to the user and let them overrule any line.
5. **Plan** (`/invest-rebalance`) and the full report:

   ```bash
   python3 scripts/longterm_advisor.py report --holdings investor/holdings-YYYY-MM-DD.xlsx \
       --decisions investor/decisions.yaml --out investor/report-YYYY-MM-DD.md
   ```
6. **Execution order**: sells first (loss-making ones in the same FY as any
   gains you book), then the satellite adds, then gold, then the index core.
   Lumpy core buys can be staggered over 3-6 months; the SIP continues monthly.
7. **Schedule the next review** with `/invest-thesis`: quarterly, after results.

## Behavioural patterns to name (kindly, with numbers)

- **IPO chasing**: many recent listings bought near listing and now below cost.
- **Averaging down without a thesis**: the losing positions are the largest.
- **Token positions**: 20+ holdings under 2% each add effort but no return.
- **Disposition effect**: winners sold early / kept tiny, losers held big.
- **No core**: an all-stock portfolio with no index base underperforms the index most years.

## Rules

- Education and tooling, not investment advice; say so once at the top of any report.
  This skill is not a SEBI-registered investment adviser.
- Every number comes from the holdings file, the policy (`config/longterm_policy.yaml`)
  or the user. When stock-specific facts (results, valuations) come from memory,
  say how old they may be and suggest checking screener.in / the company's filings.
- Never promise returns. Scenarios are ranges with probabilities, not forecasts.
- Never place, schedule or approve an order. The user executes trades in their own broker.
- Keep personal files in `investor/`; never commit them.

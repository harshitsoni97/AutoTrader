---
name: invest-research
description: >-
  Fundamentals research for Indian stocks, the long-term way: ten-year
  business quality over this quarter's price. Collects ROCE, ROE, 5-year sales
  and profit CAGR, cash conversion (OCF/PAT), debt/equity, promoter holding
  and pledge, PE vs its own 5-year median and PEG (banks: GNPA, CAR, RoA), then
  scores them 0-100 with red flags. Use when the user asks "is X a good
  long-term stock", "should I hold X", "check fundamentals", "compare X and Y",
  "quality check", or before an add/exit decision in /invest-rebalance.
argument-hint: "[NSE symbol]"
---

# invest-research

```bash
python3 scripts/longterm_advisor.py quality --metrics investor/research/SYMBOL.yaml
```

Template: `config/longterm_examples/research.example.yaml`. Set
`financial: true` for banks, NBFCs, AMCs and insurers (swaps leverage and cash
metrics for GNPA, capital adequacy and RoA).

## Where the numbers come from

In order of preference: the company's annual report and investor
presentation, NSE/BSE filings, then screener.in (consolidated view) or
Tickertape. Use WebFetch / WebSearch when they are available; otherwise ask the
user to paste the screener.in "Ratios" and "Shareholding" sections. Record the
source and date in the YAML. If a number is from memory, mark it and say it may
be stale. Never fill a missing metric with a guess; leave it out and the score
says "missing".

## Scoring (QUALITY_RULES in src/autotrader/longterm/research.py)

| metric | good | ok |
| --- | --- | --- |
| ROCE | ≥18% | ≥12% |
| ROE | ≥15% | ≥10% |
| sales CAGR 5y | ≥12% | ≥8% |
| profit CAGR 5y | ≥15% | ≥8% |
| OCF / PAT (5y) | ≥0.9 | ≥0.7 |
| debt / equity | ≤0.3 | ≤1.0 |
| promoter holding | ≥50% | ≥35% |
| promoter pledge | 0% | ≤5% |
| PE / 5y median PE | ≤1.0 | ≤1.3 |
| PEG | ≤1.2 | ≤2.0 |

75+ with no red flags: compounder-grade. 55-74: decent, watch valuation.
Below 55: needs a special reason (turnaround, cycle) written in the thesis.
New listings with under 5 years of history score "insufficient data": say that
plainly rather than scoring them on two years of numbers.

## Beyond the score (ask these out loud)

- What does the company do that a competitor cannot copy in five years?
- Who allocates the capital, and what did they do with the last big cash pile?
- Related-party transactions, auditor changes, frequent equity dilution?
- For PSUs: is the government's interest the same as the minority shareholder's?

Then `/invest-ev` for the price side, and `/invest-thesis` to write it down.

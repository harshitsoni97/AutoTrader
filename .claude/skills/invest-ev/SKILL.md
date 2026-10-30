---
name: invest-ev
description: >-
  Scenario and expected-value check for long-term Indian stock holdings.
  Builds bull/base/bear cases at a 3-5 year horizon (earnings growth x exit
  multiple), weights them by probability, and compares the expected CAGR with
  an index hurdle: if a stock does not clearly beat a Nifty index fund on its
  own numbers, the index wins. Use when the user asks "what can X return",
  "is X worth holding", "upside vs downside", "bull and bear case", "expected
  value", or when deciding hold vs exit vs add.
argument-hint: "[NSE symbol]"
---

# invest-ev

```bash
python3 scripts/longterm_advisor.py ev --scenarios investor/research/SYMBOL.yaml
```

Template: `config/longterm_examples/research.example.yaml` (the `price`,
`horizon_years`, `scenarios` keys).

## Building each scenario (show the arithmetic)

target price = today's EPS x (1 + EPS growth)^years x exit PE

- **Base**: growth near the 5-year record; exit PE near the 5-year median.
- **Bull**: what has to go right, named specifically (margin expansion, new
  segment), and an exit PE no higher than the 5-year high.
- **Bear**: growth stalls or a cycle turns; exit PE at the 5-year low or the
  sector's trough. For cyclicals use mid-cycle earnings, not peak.

Probabilities add to 1. Default hurdle 11%/yr (a long-run Nifty 50 assumption,
adjustable with `hurdle_cagr_pct`). Add `dividend_yield_pct` for PSUs and REITs.

## Reading the verdict

| expected CAGR vs hurdle | meaning |
| --- | --- |
| +3 points or more | earns its place as a satellite |
| within ±2 points | index-like: own the index instead unless it diversifies |
| below | the index is the better bet on these numbers |

Always quote the bear case's loss next to the expected CAGR. A good EV with a
-60% bear case is still sized small. These are ranges for a decision, not
forecasts; never present a target as a prediction.

---
name: invest-thesis
description: >-
  Investment thesis journal and quarterly review for a long-term Indian
  portfolio. Writes a one-page thesis per holding (why own it, what must go
  right, what would make you sell, size limit) to investor/theses/SYMBOL.md,
  then at each quarterly results season checks every holding against its own
  sell triggers and records keep / add / trim / exit. Use when the user says
  "why do I own X", "write my thesis", "quarterly review", "results are out",
  "should I still hold", or after /invest-rebalance to document the keepers.
argument-hint: "[symbol | review]"
---

# invest-thesis

Long-term investing fails mostly from forgetting why something was bought.
A thesis written before the price moves is the defence against both panic
selling and hope-holding.

## Write a thesis: `investor/theses/SYMBOL.md`

```markdown
# SYMBOL — Company name            bucket: satellite | core
Written: YYYY-MM-DD   Price: ₹…   Weight: …%   Max weight: …%

## Why I own it (two lines)
## What must go right (3 measurable things, with the number and the date)
- e.g. Blinkit contribution margin positive by FY27
## What would make me sell (thesis breakers, not price levels)
- e.g. promoter pledge above 10%; two straight quarters of falling ROCE
## Valuation guard
Add below ₹… (PE …), trim above ₹… (PE …). From /invest-ev.
## Review log
| date | result / event | thesis intact? | action |
```

If the user cannot fill "why I own it" in two lines, say so: the honest
action is usually to let the index hold it.

## Quarterly review (`review`)

Run after each results season (mid-Feb, mid-May, mid-Aug, mid-Nov):

1. `python3 scripts/longterm_advisor.py analyze --holdings <latest export>`
2. For each holding: read the quarter's result (filings / investor
   presentation), check every "what must go right" and "what would make me
   sell" line, append one row to the review log.
3. Collect the actions into `investor/decisions.yaml` and run `/invest-rebalance`.
4. Once a year (Jan-Feb, before the FY ends) also run `/invest-tax-harvest`.

Price falls alone are not a thesis break. A thesis break with a price rise is
still a sell. Write which one it is.

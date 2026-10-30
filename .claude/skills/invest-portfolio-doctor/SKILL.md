---
name: invest-portfolio-doctor
description: >-
  Portfolio Doctor for Indian long-term investors. Reads a broker holdings
  export (.xlsx/.csv from Zerodha Console, Groww, Upstox; header row found
  automatically) and reports value, P&L, weights, sector and asset mix,
  business-group exposure (e.g. TMPV+TMCV, Tata group, PSUs), concentration
  (top 5, HHI, effective number of holdings), token positions, oversize
  positions, deep losers and the missing index core. Use when the user says
  "analyse my holdings", "how diversified am I", "what's wrong with my
  portfolio", "too many stocks", "sector exposure", or uploads a holdings file.
argument-hint: "[holdings file]"
---

# invest-portfolio-doctor

```bash
python3 scripts/longterm_advisor.py analyze --holdings investor/holdings.xlsx          # markdown
python3 scripts/longterm_advisor.py analyze --holdings investor/holdings.xlsx --json   # for follow-ups
```

Limits come from `portfolio_policy` in `config/longterm_policy.yaml`:

| check | default | why |
| --- | --- | --- |
| holdings | 12-20 | beyond ~20 names a small portfolio tracks the index with extra effort |
| min position | 2% | smaller cannot move results; build it or drop it |
| single stock | 10% | one bad quarter should not sink the plan |
| group | 15% | demerged twins and one promoter group move together |
| sector | 25% | sector cycles last years in India (PSU, IT, NBFC) |
| deep loss | -30% | triggers a thesis check, never an automatic sell |
| index core | 50% | the low-cost base that the satellites must beat |
| asset mix | 85 / 10 / 5 equity / gold / debt | gold is India's classic hedge against rupee and equity falls |

ETFs, gold and REITs (`-RR` symbols) are recognised via `asset_class_overrides`;
add new ones there. Add business groups under `groups`.

## How to present it

1. One line: value, P&L, number of holdings.
2. The 3-5 flags that matter most, in order of rupees at stake, plain words.
   "TMPV and TMCV are one bet on Tata Motors: 23.5% of your money."
3. Sector and asset-mix tables only if asked or if a flag points at them.
4. Point to `/invest-tax-harvest` and `/invest-rebalance` as the next step.

Prices in the export are the previous close. Say so if the user is acting today.

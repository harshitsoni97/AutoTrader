---
name: invest-rebalance
description: >-
  Rebalance planner and SIP splitter for Indian long-term portfolios. Turns a
  decisions file (exit / trim / hold / add per symbol) into an exact list of
  sells and buys in whole shares, enforces the single-stock cap, tops gold up
  to its target, sends the rest to an index-ETF core (Nifty 50, Next 50,
  Midcap 150), and estimates the capital-gains tax and costs of the whole
  plan. Also splits a monthly SIP. Use when the user says "rebalance",
  "what should I buy/sell", "consolidate my portfolio", "I have ₹X to
  invest", "set up a SIP", or after /invest-portfolio-doctor.
argument-hint: "[decisions file]"
---

# invest-rebalance

```bash
python3 scripts/longterm_advisor.py rebalance --holdings investor/holdings.xlsx --decisions investor/decisions.yaml
```

Start from `config/longterm_examples/decisions.example.yaml`; copy it to
`investor/decisions.yaml` and fill one line per holding:

| action | effect |
| --- | --- |
| `exit` | sell everything |
| `trim` | sell down to `target_pct` (default: single-stock cap) |
| `hold` | keep; still trimmed if over the single-stock cap |
| `add` | buy up to `target_pct` from freed cash / `new_cash_inr` |
| (none) | kept as-is, listed under "undecided": ask the user about each |

Order of money: sells -> adds -> gold to its asset-mix target -> index core by
`core_instruments` weights. New instruments need a price in `prices:` to get a
share count; without one the plan shows rupees only. Never invent a price.

## Choosing actions (agree them with the user)

- Two holdings doing the same job (two delivery apps, two PSU power utilities,
  a hotel chain and its smaller peer): keep the leader.
- Token positions in Nifty 50 names are already owned through NIFTYBEES:
  either build them to a real weight with a thesis or let the index hold them.
- A position that is down a lot is not a reason to hold ("getting back to
  cost") or to sell; the question is whether you would buy it today at this price.
- Prefer exits that book short-term losses or tax-free long-term gains.

## SIP

`sip_policy` in `config/longterm_policy.yaml` splits each month's amount:
by default 60% core ETFs, 25% the most under-weight conviction holding,
10% gold, 5% debt/liquid. A SIP into index mutual funds works just as well
as ETFs and avoids lot sizes; say so if the user prefers funds.

## Output

Show the trade table, the tax line (realised gains/losses, estimated tax, loss
carried forward), the costs, the holdings count before/after, and the weights
after. Then: "this is a plan, not an order; you place each trade in your broker".

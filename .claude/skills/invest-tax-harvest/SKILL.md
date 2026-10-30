---
name: invest-tax-harvest
description: >-
  Indian capital-gains tax planner for listed equity (Sec 111A / 112A). Splits
  every holding into long-term (>12 months) and short-term shares, estimates
  unrealised STCG/STCL/LTCG/LTCL, applies the set-off rules (STCL against STCG
  then LTCG, LTCL against LTCG only, 8-year carry forward) and the ₹1.25 lakh
  yearly LTCG exemption, and lists tax-loss and LTCG harvesting candidates.
  Use when the user asks "how much tax if I sell", "tax-loss harvesting",
  "book LTCG tax free", "set off my losses", "before 31 March", or before any
  rebalance.
argument-hint: "[holdings file]"
---

# invest-tax-harvest

```bash
python3 scripts/longterm_advisor.py tax --holdings investor/holdings.xlsx --ytd investor/decisions.yaml
```

`--pnl investor/pnl.xlsx` imports the broker's P&L statement for this FY
(Zerodha Console format: a `Symbol` / `Realized P&L` table). It has no buy dates,
so each line counts as short-term unless `realised_terms: {SYMBOL: long}` in the
decisions file says otherwise; ask the user, or check the tradebook.

`--ytd` reads `realised_ytd: {stcg, stcl, ltcg, ltcl}` (what was already booked
this financial year; the broker's tax P&L report has it). Without it the full
₹1.25L exemption is assumed unused.

## Rules it applies (edit `tax_policy` when the Finance Act changes)

| | rate | notes |
| --- | --- | --- |
| STCG (≤12 months) | 20% + 4% cess | no exemption |
| LTCG (>12 months) | 12.5% + 4% cess | first ₹1.25L per FY exempt |
| STCL | offsets STCG, then LTCG | most valuable loss |
| LTCL | offsets LTCG only | carry forward 8 years, only if the ITR is filed on time |

Gold ETFs, REITs and debt are listed as "not equity-taxed" and left out.

## What to tell the user

- **Short-term losses are the valuable ones**: they cancel 20% STCG. Book them
  in the same FY as any short-term gains.
- **LTCG harvesting**: when there are no big losses this year, sell enough
  long-term winners to book up to ₹1.25L of gains tax-free and rebuy, raising
  the cost base. If the year already has long-term losses, they absorb the
  gains first and the exemption is wasted: say so, and suggest harvesting in
  the next FY instead.
- **Set-off is compulsory**: you cannot keep losses aside for later while
  booking gains in the same year.
- **Rebuying**: no wash-sale rule exists in India; rebuying on a later day and
  keeping contract notes is the usual practice. Never call it risk-free.
- **Friction**: each sell costs ~0.1% STT plus ~₹16 DP charge per scrip per day;
  selling a ₹40 position to harvest ₹5 of loss is not worth it.

## UK tax-resident investors

Set `residency: {uk_tax_resident: true, gbp_inr: <rate>}` (and usually
`use_funds: false`) in the user's own `investor/decisions.yaml`, never in the
repo. Then `rebalance`/`report` add a UK view. What changes:

- **Both countries tax the gain.** India taxes first; the UK taxes too (CGT 18%/24% above the £3,000 annual
  exempt amount) and credits the Indian tax under the India-UK treaty. The UK tax year runs 6 April to 5 April.
- **Indian ETFs and mutual funds are UK non-reporting offshore funds.** Gains are taxed as income, and losses
  are not allowable. Don't plan to buy them; hold index exposure in a UK ISA instead.
- **The UK uses one pooled average cost per share** (section 104), not FIFO, plus a 30-day rule: a rebuy of
  the same share within 30 days of a sale is matched to that sale. "Sell and rebuy" harvesting does nothing
  for UK tax.
- **Gains are measured in pounds**, at each date's exchange rate. A weaker rupee shrinks rupee gains and
  deepens rupee losses.
- **Losses must be claimed.** Report them on Self Assessment within 4 years and they carry forward with no
  time limit. Dividends are taxable in the UK too, with credit for Indian TDS.
- **New arrivals:** the 4-year Foreign Income and Gains (FIG) regime can exempt foreign gains. Ask when the
  user became UK resident.
- **Indian residential status:** a UK resident is usually an Indian NRI. Under FEMA this starts on leaving India
  for work or to live there, not after 182 days. Record `india_status` and `india_account` in `residency`; if an NRI
  still trades through a resident account, say so first, before any trade. That means NRI (NRO/NRE, PIS)
  accounts instead of resident ones, no intraday trading, and TDS deducted by the broker on gains. Ask the
  user to confirm their account type.

## Caveats to state

- Gains use the broker's blended average price, not lot-wise cost.
- Demat sales are FIFO: long-term shares leave first.
- Pre-2018 grandfathering is not applied.
- Not tax advice; a CA should confirm before filing.

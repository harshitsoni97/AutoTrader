"""UK view for a UK-tax-resident investor holding Indian shares (indicative).

India taxes the gain first (source country); the UK taxes it too and gives a
credit for the Indian tax under the India-UK treaty. Differences that change
decisions:
- Indian ETFs/mutual funds are UK non-reporting offshore funds: gains are taxed
  as income, losses are not allowable, so the plan should not buy them.
- UK uses one pooled average cost per share (section 104), not India's FIFO,
  and the 30-day rule matches a sale with a rebuy of the same share within
  30 days after it, so "sell and rebuy" harvesting does not work for UK tax.
- Gains are computed in pounds: cost at the purchase-date rate, proceeds at
  the sale-date rate. This module uses one rate for both, so it shows the
  sign and rough size only.
"""
from __future__ import annotations

from .holdings import Holding
from .policy import asset_class


def uk_plan_view(holdings: list[Holding], policy: dict, plan: dict, residency: dict,
                 realised_ytd_inr: float = 0.0) -> dict:
    ukp = policy["uk_tax_policy"]
    fx = float(residency.get("gbp_inr") or 0)
    by_sym = {h.symbol: h for h in holdings}
    fund_classes = set(ukp["offshore_fund_classes"])
    sells = [t for t in plan["trades"] if t["side"] == "SELL"]
    buys = [t for t in plan["trades"] if t["side"] == "BUY"]

    def is_fund(sym: str) -> bool:
        h = by_sym.get(sym)
        return asset_class(sym, h.sector if h else "ETF" if sym.endswith("BEES") else "", policy) in fund_classes

    cgt_net_inr = sum(t.get("gain", 0) for t in sells if not is_fund(t["symbol"])) + realised_ytd_inr
    fund_gain_inr = sum(t.get("gain", 0) for t in sells if is_fund(t["symbol"]))
    notes = []
    fund_buys = sorted({t["symbol"] for t in buys if is_fund(t["symbol"])})
    if fund_buys:
        notes.append(f"Plan buys Indian funds ({', '.join(fund_buys)}): non-reporting offshore funds for UK tax, "
                     "so future gains are taxed as income and losses are not allowable. Set use_funds: false "
                     "in your decisions file, or hold index funds in a UK ISA instead.")
    held_funds = sorted(h.symbol for h in holdings if is_fund(h.symbol))
    if held_funds:
        notes.append(f"Already held Indian funds ({', '.join(held_funds)}): a sale is an offshore income gain "
                     "(taxed as income).")
    out = {
        "gbp_inr": fx or None,
        "cgt_net_inr": round(cgt_net_inr, 2),
        "cgt_net_gbp": round(cgt_net_inr / fx, 2) if fx else None,
        "offshore_income_gain_inr": round(fund_gain_inr, 2),
        "annual_exempt_amount_gbp": ukp["annual_exempt_amount_gbp"],
        "proceeds_gbp": round(plan["sells_total"] / fx, 2) if fx else None,
        "notes": notes,
    }
    fig_until = residency.get("fig_eligible_until")
    if fig_until:
        notes.append(f"FIG regime available until {fig_until}: claiming it exempts that year's foreign gains and "
                     "income from UK tax, but costs your personal allowance and the CGT annual exempt amount, and "
                     "foreign losses in that year can't be used. "
                     + ("With a net foreign loss this year, don't claim it." if cgt_net_inr < 0 else
                        "Claim only if the foreign gains and income outweigh the allowances lost."))
    if residency.get("india_status") == "NRI" and str(residency.get("india_account", "")).startswith("resident"):
        notes.insert(0, "ACCOUNT FIRST: you are an NRI but the trading account is a resident one. Under FEMA, ask "
                        "the broker to move the holdings to an NRI (NRO non-PIS) demat/trading account linked to "
                        "your NRO bank account before placing these trades. Sales from an NRI account have TDS "
                        "deducted by the broker.")
    if fx:
        if cgt_net_inr < 0:
            notes.append(f"Net capital loss of about £{-cgt_net_inr / fx:,.0f} this UK tax year: no UK CGT. Claim it "
                         "on your Self Assessment (within 4 years) to carry it forward against future UK gains.")
        elif cgt_net_inr / fx <= ukp["annual_exempt_amount_gbp"]:
            notes.append("Net gain within the £%s annual exempt amount: no UK CGT." % f"{ukp['annual_exempt_amount_gbp']:,}")
        else:
            notes.append("Net gain above the annual exempt amount: UK CGT at 18%/24% on the excess, "
                         "less a credit for Indian tax paid on the same gains.")
    return out

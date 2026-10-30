"""Indian capital-gains tax on listed equity (Sec 111A / 112A) for long-term investors.

Approximations, stated plainly so nobody mistakes this for a tax return:
- Brokers report one blended average price. Real lots have different costs, so
  gains per lot are estimated as (LTP - average) x quantity.
- Demat sales are FIFO: the oldest shares (long-term ones first) go out first.
- Grandfathering for shares bought before 1 Feb 2018 is not applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .holdings import Holding
from .policy import asset_class


@dataclass
class Realised:
    stcg: float = 0.0   # short-term gains (positive)
    stcl: float = 0.0   # short-term losses (positive number)
    ltcg: float = 0.0
    ltcl: float = 0.0
    lines: list = field(default_factory=list)

    def add(self, other: "Realised") -> None:
        self.stcg += other.stcg
        self.stcl += other.stcl
        self.ltcg += other.ltcg
        self.ltcl += other.ltcl
        self.lines.extend(other.lines)


def split_sale(h: Holding, qty: float) -> Realised:
    """Gain/loss from selling `qty` shares of `h` at its LTP, FIFO (long-term first)."""
    qty = min(qty, h.qty)
    lt = min(qty, h.qty_lt or 0.0)
    st = qty - lt
    per_share = h.ltp - h.avg_price
    r = Realised()
    for bucket_qty, gain_key, loss_key in ((lt, "ltcg", "ltcl"), (st, "stcg", "stcl")):
        amt = bucket_qty * per_share
        if amt > 0:
            setattr(r, gain_key, getattr(r, gain_key) + amt)
        elif amt < 0:
            setattr(r, loss_key, getattr(r, loss_key) - amt)
    r.lines.append({"symbol": h.symbol, "qty": qty, "lt_qty": lt, "st_qty": st,
                    "gain": round(qty * per_share, 2)})
    return r


def compute_tax(r: Realised, tp: dict, ltcg_exemption_used: float = 0.0,
                carried_stcl: float = 0.0, carried_ltcl: float = 0.0) -> dict:
    """Set-off per the Income Tax Act:
    STCL offsets STCG first, then LTCG. LTCL offsets LTCG only.
    Leftover losses carry forward 8 years if the return is filed on time.
    """
    stcl = r.stcl + carried_stcl
    ltcl = r.ltcl + carried_ltcl
    stcg = r.stcg
    ltcg = r.ltcg

    off = min(stcg, stcl)
    stcg, stcl = stcg - off, stcl - off
    off = min(ltcg, ltcl)
    ltcg, ltcl = ltcg - off, ltcl - off
    off = min(ltcg, stcl)
    ltcg, stcl = ltcg - off, stcl - off

    exemption = max(0.0, tp["ltcg_exemption_inr"] - ltcg_exemption_used)
    taxable_ltcg = max(0.0, ltcg - exemption)
    cess = 1 + tp["cess_pct"] / 100
    tax = (stcg * tp["stcg_rate_pct"] / 100 + taxable_ltcg * tp["ltcg_rate_pct"] / 100) * cess
    return {
        "gross": {"stcg": round(r.stcg, 2), "stcl": round(r.stcl, 2),
                  "ltcg": round(r.ltcg, 2), "ltcl": round(r.ltcl, 2)},
        "net_stcg": round(stcg, 2),
        "net_ltcg": round(ltcg, 2),
        "ltcg_exempt_used": round(min(ltcg, exemption), 2),
        "taxable_ltcg": round(taxable_ltcg, 2),
        "tax_inr": round(tax, 2),
        "carry_forward_stcl": round(stcl, 2),
        "carry_forward_ltcl": round(ltcl, 2),
    }


def tax_report(holdings: list[Holding], policy: dict, realised_ytd: dict | None = None) -> dict:
    """Unrealised gains by bucket plus harvesting ideas for the current financial year.

    realised_ytd: what was already booked this FY, e.g.
      {"stcg": 0, "stcl": 0, "ltcg": 0, "ltcl": 0}
    """
    tp = policy["tax_policy"]
    ytd = Realised(**{k: float(v) for k, v in (realised_ytd or {}).items() if k in ("stcg", "stcl", "ltcg", "ltcl")})

    unreal = Realised()
    rows = []
    unknown_period = []
    non_equity = []
    for h in holdings:
        cls = asset_class(h.symbol, h.sector, policy)
        if cls in tp["non_equity_classes"]:
            non_equity.append(h.symbol)
            continue
        if h.qty_lt is None:
            unknown_period.append(h.symbol)
        r = split_sale(h, h.qty)
        unreal.add(r)
        rows.append({
            "symbol": h.symbol,
            "lt_qty": r.lines[0]["lt_qty"], "st_qty": r.lines[0]["st_qty"],
            "lt_gain": round(r.ltcg - r.ltcl, 2), "st_gain": round(r.stcg - r.stcl, 2),
            "pnl_pct": round(h.pnl_pct, 2),
        })

    # Tax-loss harvesting: every loss is worth something; a short-term loss is
    # the most flexible (it can offset STCG at 20% or LTCG at 12.5%).
    loss_rows = sorted((x for x in rows if x["lt_gain"] < 0 or x["st_gain"] < 0),
                       key=lambda x: min(x["st_gain"], 0) + min(x["lt_gain"], 0))
    # LTCG harvesting: book long-term gains up to the yearly exemption and rebuy,
    # resetting the cost base higher for free.
    room = max(0.0, tp["ltcg_exemption_inr"] - max(0.0, ytd.ltcg - ytd.ltcl))
    ltcg_harvest, filled = [], 0.0
    for x in sorted((x for x in rows if x["lt_gain"] > 0), key=lambda x: -x["lt_gain"]):
        if filled >= room:
            break
        take = min(x["lt_gain"], room - filled)
        filled += take
        ltcg_harvest.append({"symbol": x["symbol"], "lt_gain": x["lt_gain"], "harvest": round(take, 2),
                             "fraction": round(take / x["lt_gain"], 3)})

    if_all_sold = compute_tax(Realised(stcg=unreal.stcg + ytd.stcg, stcl=unreal.stcl + ytd.stcl,
                                       ltcg=unreal.ltcg + ytd.ltcg, ltcl=unreal.ltcl + ytd.ltcl), tp)
    return {
        "unrealised": {"stcg": round(unreal.stcg, 2), "stcl": round(unreal.stcl, 2),
                       "ltcg": round(unreal.ltcg, 2), "ltcl": round(unreal.ltcl, 2)},
        "realised_ytd": {"stcg": ytd.stcg, "stcl": ytd.stcl, "ltcg": ytd.ltcg, "ltcl": ytd.ltcl},
        "if_everything_sold_today": if_all_sold,
        "loss_harvest_candidates": loss_rows,
        "ltcg_exemption_room": round(room, 2),
        "ltcg_harvest_plan": ltcg_harvest,
        "by_holding": rows,
        "unknown_holding_period": unknown_period,
        "skipped_non_equity": non_equity,
        "notes": [
            "Gains use the broker's blended average price; actual lot-wise gains will differ.",
            "Sales are FIFO: long-term shares are sold before short-term ones.",
            "Losses only carry forward if the ITR is filed by the due date.",
            "There is no wash-sale rule in India, but a sell-and-rebuy done only to book a loss "
            "can be questioned; rebuying on a later day and keeping the records is the usual practice.",
        ],
    }

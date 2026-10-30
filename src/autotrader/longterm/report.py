"""Markdown rendering for the advisor's outputs."""
from __future__ import annotations


def inr(x: float | None, decimals: int = 0) -> str:
    """Indian digit grouping: 1234567 -> ₹12,34,567."""
    if x is None:
        return "-"
    sign = "-" if x < 0 else ""
    x = abs(x)
    whole, frac = f"{x:.{decimals}f}".partition(".")[::2]
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{sign}₹{whole}" + (f".{frac}" if frac else "")


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_analysis(a: dict) -> str:
    s = a["summary"]
    lines = [
        "## Portfolio snapshot",
        "",
        f"- **{s['holdings']} holdings**, invested {inr(s['invested'])}, worth {inr(s['value'])} "
        f"({inr(s['pnl'])}, {s['pnl_pct']:+.1f}%)",
        f"- Top 5 = {s['top5_pct']:.1f}% of the portfolio; effective number of holdings "
        f"{s['effective_holdings']} (1 / HHI)",
        f"- {s['winners']} winners (+{inr(s['gain_from_winners'])}), {s['losers']} losers "
        f"({inr(s['loss_from_losers'])})",
        f"- Index core: {s['core_index_pct']:.1f}%",
        "",
        "### Flags",
        "",
    ]
    lines += [f"- **{f['kind']}**: {f['detail']}" for f in a["flags"]] or ["- none"]
    lines += ["", "### Asset mix", "", _table(["bucket", "weight"],
                                               [[k, f"{v:.1f}%"] for k, v in a["asset_mix"].items()])]
    lines += ["", "### Sectors", "", _table(["sector", "weight"],
                                             [[k, f"{v:.1f}%"] for k, v in a["sectors"].items()])]
    lines += ["", "### Holdings", "", _table(
        ["symbol", "sector", "qty (LT)", "avg", "LTP", "value", "weight", "P&L", "P&L %"],
        [[r["symbol"], r["sector"], f"{r['qty']:g} ({'?' if r['qty_lt'] is None else format(r['qty_lt'], 'g')})",
          f"{r['avg_price']:.2f}", f"{r['ltp']:.2f}", inr(r["value"]), f"{r['weight_pct']:.1f}%",
          inr(r["pnl"]), f"{r['pnl_pct']:+.1f}%"] for r in a["holdings"]])]
    return "\n".join(lines)


def render_tax(t: dict) -> str:
    u, all_ = t["unrealised"], t["if_everything_sold_today"]
    lines = [
        "## Capital gains (equity, Sec 111A / 112A)",
        "",
        _table(["", "short-term", "long-term"],
               [["unrealised gains", inr(u["stcg"]), inr(u["ltcg"])],
                ["unrealised losses", inr(-u["stcl"]), inr(-u["ltcl"])]]),
        "",
        f"If everything were sold today: tax {inr(all_['tax_inr'])}, losses carried forward "
        f"{inr(all_['carry_forward_stcl'])} short-term + {inr(all_['carry_forward_ltcl'])} long-term.",
        "",
        f"LTCG exemption room left this FY: {inr(t['ltcg_exemption_room'])}.",
        "",
        "### Loss-harvest candidates (biggest first)",
        "",
        _table(["symbol", "ST qty", "ST gain/loss", "LT qty", "LT gain/loss", "P&L %"],
               [[x["symbol"], f"{x['st_qty']:g}", inr(x["st_gain"]), f"{x['lt_qty']:g}", inr(x["lt_gain"]),
                 f"{x['pnl_pct']:+.1f}%"] for x in t["loss_harvest_candidates"]]),
    ]
    if t["ltcg_harvest_plan"]:
        lines += ["", "### LTCG harvest (book tax-free up to the exemption, then rebuy)", "",
                  _table(["symbol", "LT gain", "harvest", "share of LT qty"],
                         [[x["symbol"], inr(x["lt_gain"]), inr(x["harvest"]), f"{x['fraction'] * 100:.0f}%"]
                          for x in t["ltcg_harvest_plan"]])]
    if t["skipped_non_equity"]:
        lines += ["", f"Not equity-taxed, check separately: {', '.join(t['skipped_non_equity'])}."]
    if t["unknown_holding_period"]:
        lines += ["", f"Holding period unknown (treated as short-term): {', '.join(t['unknown_holding_period'])}."]
    lines += ["", "Notes:"] + [f"- {n}" for n in t["notes"]]
    return "\n".join(lines)


def render_rebalance(p: dict) -> str:
    tx = p["tax"]
    lines = [
        "## Rebalance plan (proposal only: nothing is placed)",
        "",
        _table(["side", "symbol", "qty", "price", "amount", "gain on sale", "why"],
               [[t["side"], t["symbol"], "-" if t["qty"] is None else f"{t['qty']:g}",
                 "-" if t["price"] is None else f"{t['price']:.2f}", inr(t["amount"]),
                 inr(t["gain"]) if "gain" in t else "", t.get("why", "")] for t in p["trades"]]),
        "",
        f"- Sells {inr(p['sells_total'])}, buys {inr(p['buys_total'])}, est. costs {inr(p['est_costs'])}, "
        f"cash left {inr(p['cash_left'])}",
        f"- Realised: STCG {inr(tx['gross']['stcg'])}, STCL {inr(-tx['gross']['stcl'])}, "
        f"LTCG {inr(tx['gross']['ltcg'])}, LTCL {inr(-tx['gross']['ltcl'])}",
        f"- **Estimated tax {inr(tx['tax_inr'])}**; losses to carry forward: "
        f"{inr(tx['carry_forward_stcl'])} ST + {inr(tx['carry_forward_ltcl'])} LT",
        f"- After: {p['post_holdings']} holdings, index core {p['post_core_pct']:.1f}%",
    ]
    if p["undecided"]:
        lines.append(f"- Held as-is (no decision yet): {', '.join(p['undecided'])}")
    lines += ["", "### Weights after", "",
              _table(["symbol", "weight"], [[s, f"{w:.1f}%"] for s, w in p["post_weights_pct"].items()])]
    return "\n".join(lines)

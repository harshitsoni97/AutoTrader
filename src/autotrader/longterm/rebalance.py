"""Turn per-holding decisions into a rebalance plan with tax impact.

Decisions file (YAML), one entry per symbol you have a view on:

    decisions:
      TMPV:  {action: trim, target_pct: 7, why: "group cap with TMCV"}
      IDEA:  {action: exit, why: "tiny, no thesis"}
      SBIN:  {action: add, target_pct: 6}
      TITAN: {action: hold}
    new_cash_inr: 0            # fresh money to deploy alongside
    prices: {NIFTYBEES: 290}   # optional, to turn new buys into share counts
    realised_ytd: {stcg: 0, stcl: 0, ltcg: 0, ltcl: 0}

Actions: exit (to 0) | trim (down to target_pct, default max_position_pct) |
add (up to target_pct) | hold (keep; still trimmed to the single-stock cap).
Anything without a decision is held as-is and listed under "undecided".
Money freed is sent to the gold target first, then the core: index ETFs, or
the direct-stock `core_stocks` when `use_funds` is false (no funds bought,
gold/debt targets reported only).
Nothing is ever placed: this writes a plan for a human to execute.
"""
from __future__ import annotations

import math

from .analysis import MIX_BUCKET
from .holdings import Holding
from .policy import asset_class, core_list
from .tax import Realised, compute_tax, split_sale


def plan_rebalance(holdings: list[Holding], policy: dict, decisions: dict) -> dict:
    pp, tp = policy["portfolio_policy"], policy["tax_policy"]
    dec = {k.upper(): v or {} for k, v in (decisions.get("decisions") or {}).items()}
    prices = {k.upper(): float(v) for k, v in (decisions.get("prices") or {}).items()}
    new_cash = float(decisions.get("new_cash_inr") or 0)

    total = sum(h.value for h in holdings) + new_cash
    by_sym = {h.symbol: h for h in holdings}
    trades, undecided = [], []
    realised = Realised()
    cash = new_cash
    after: dict[str, float] = {}

    # 1. sells
    for h in holdings:
        d = dec.get(h.symbol)
        action = (d or {}).get("action", "hold")
        if d is None:
            undecided.append(h.symbol)
        target_value = h.value
        if action == "exit":
            target_value = 0.0
        elif action == "trim":
            target_value = min(h.value, total * float(d.get("target_pct", pp["max_position_pct"])) / 100)
        elif action in ("hold", "add") and asset_class(h.symbol, h.sector, policy) == "equity":
            target_value = min(h.value, total * pp["max_position_pct"] / 100)

        if target_value < h.value:
            qty = h.qty if target_value == 0 else math.ceil((h.value - target_value) / h.ltp)
            qty = min(qty, h.qty)
            proceeds = qty * h.ltp
            r = split_sale(h, qty)
            realised.add(r)
            cost = proceeds * tp["sell_cost_pct"] / 100 + tp["dp_charge_inr"]
            cash += proceeds - cost
            trades.append({"side": "SELL", "symbol": h.symbol, "qty": qty, "price": h.ltp,
                           "amount": round(proceeds, 2), "est_costs": round(cost, 2),
                           "gain": r.lines[0]["gain"], "lt_qty": r.lines[0]["lt_qty"],
                           "st_qty": r.lines[0]["st_qty"], "action": action,
                           "why": (d or {}).get("why", "over the single-stock cap" if action != "exit" else "")})
            after[h.symbol] = h.value - proceeds
        else:
            after[h.symbol] = h.value

    # 2. adds to existing holdings
    for sym, d in dec.items():
        if d.get("action") != "add":
            continue
        tgt = total * float(d.get("target_pct", pp["min_position_pct"] * 2)) / 100
        price = by_sym[sym].ltp if sym in by_sym else prices.get(sym)
        need = tgt - after.get(sym, 0.0)
        if need <= 0 or cash <= 0:
            continue
        amt = min(need, cash)
        qty = math.floor(amt / price) if price else None
        if qty == 0:
            continue
        spend = qty * price if qty else amt
        cash -= spend
        after[sym] = after.get(sym, 0.0) + spend
        trades.append({"side": "BUY", "symbol": sym, "qty": qty, "price": price, "amount": round(spend, 2),
                       "action": "add", "why": d.get("why", f"build to {d.get('target_pct')}%")})

    # 3. gold up to its target, then the index core with the rest
    gold_target = total * float((pp.get("asset_mix_target_pct") or {}).get("gold", 0)) / 100
    gold_have = sum(v for s, v in after.items()
                    if s in by_sym and MIX_BUCKET.get(asset_class(s, by_sym[s].sector, policy)) == "gold")
    gold_sym = next((s for s in by_sym if MIX_BUCKET.get(asset_class(s, by_sym[s].sector, policy)) == "gold"),
                    "GOLDBEES")
    buys, notes = [], []
    use_funds = pp.get("use_funds", True)
    if not use_funds and gold_target > gold_have:
        notes.append(f"gold is {gold_have / total * 100:.1f}% vs {gold_target / total * 100:.0f}% target; "
                     "funds are switched off, so nothing is bought (Sovereign Gold Bonds on the exchange "
                     "are the non-fund route)")
    if use_funds and gold_target > gold_have and cash > 0:
        buys.append((gold_sym, min(gold_target - gold_have, cash), "gold up to its asset-mix target"))
        cash -= buys[-1][1]
    core = core_list(policy)
    wsum = sum(c["weight"] for c in core) or 1
    if cash > 0:
        for c in core:
            buys.append((c["symbol"], cash * c["weight"] / wsum, f"core: {c.get('name', c['symbol'])}"))
        cash = 0.0
    for sym, amt, why in buys:
        price = by_sym[sym].ltp if sym in by_sym else prices.get(sym)
        qty = math.floor(amt / price) if price else None
        spend = qty * price if qty else amt
        cash += amt - spend
        after[sym] = after.get(sym, 0.0) + spend
        trades.append({"side": "BUY", "symbol": sym, "qty": qty, "price": price, "amount": round(spend, 2),
                       "action": "redeploy", "why": why})

    ytd = decisions.get("realised_ytd") or {}
    tax = compute_tax(Realised(stcg=realised.stcg + float(ytd.get("stcg", 0)),
                               stcl=realised.stcl + float(ytd.get("stcl", 0)),
                               ltcg=realised.ltcg + float(ytd.get("ltcg", 0)),
                               ltcl=realised.ltcl + float(ytd.get("ltcl", 0))), tp)

    post = {s: round(v / total * 100, 2) for s, v in sorted(after.items(), key=lambda kv: -kv[1]) if v > 1}
    core_syms = {c["symbol"] for c in core}
    return {
        "trades": trades,
        "sells_total": round(sum(t["amount"] for t in trades if t["side"] == "SELL"), 2),
        "buys_total": round(sum(t["amount"] for t in trades if t["side"] == "BUY"), 2),
        "est_costs": round(sum(t.get("est_costs", 0) for t in trades), 2),
        "cash_left": round(cash, 2),
        "tax": tax,
        "post_weights_pct": post,
        "post_holdings": len(post),
        "post_core_pct": round(sum(v for s, v in post.items() if s in core_syms), 2),
        "undecided": undecided,
        "notes": notes,
    }

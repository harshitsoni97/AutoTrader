"""Portfolio diagnostics: allocation, concentration, fragmentation and flags."""
from __future__ import annotations

from collections import defaultdict

from .holdings import Holding
from .policy import EQUITY_CLASSES, asset_class

MIX_BUCKET = {"equity": "equity", "equity_index": "equity", "gold": "gold", "silver": "gold",
              "debt": "debt", "reit": "real_estate"}


def analyze(holdings: list[Holding], policy: dict) -> dict:
    pp = policy["portfolio_policy"]
    total_value = sum(h.value for h in holdings)
    total_invested = sum(h.invested for h in holdings)
    if total_value <= 0:
        raise ValueError("portfolio has no value")

    rows = []
    sectors: dict[str, float] = defaultdict(float)
    mix: dict[str, float] = defaultdict(float)
    for h in sorted(holdings, key=lambda x: -x.value):
        cls = asset_class(h.symbol, h.sector, policy)
        w = h.value / total_value * 100
        sectors[h.sector if cls in EQUITY_CLASSES else cls.upper()] += w
        mix[MIX_BUCKET.get(cls, "equity")] += w
        rows.append({**h.to_dict(), "weight_pct": round(w, 2), "asset_class": cls})

    groups = {}
    for name, members in (pp.get("groups") or {}).items():
        held = [r for r in rows if r["symbol"] in members]
        if len(held) >= 1:
            groups[name] = {"weight_pct": round(sum(r["weight_pct"] for r in held), 2),
                            "members": [r["symbol"] for r in held]}

    weights = [r["weight_pct"] / 100 for r in rows]
    hhi = sum(w * w for w in weights)
    core_pct = sum(r["weight_pct"] for r in rows if r["asset_class"] == "equity_index")

    flags = []
    n = len(rows)
    if n > pp["target_holdings_max"]:
        flags.append({"kind": "fragmented", "detail":
                      f"{n} holdings vs a target of {pp['target_holdings_min']}-{pp['target_holdings_max']}: "
                      f"{n - pp['target_holdings_max']}+ too many to follow properly"})
    tiny = [r for r in rows if r["weight_pct"] < pp["min_position_pct"]]
    if tiny:
        flags.append({"kind": "tiny_positions", "symbols": [r["symbol"] for r in tiny],
                      "detail": f"{len(tiny)} positions under {pp['min_position_pct']}% "
                                f"({sum(r['weight_pct'] for r in tiny):.1f}% of the portfolio in total): "
                                "too small to move results either way"})
    for r in rows:
        if r["asset_class"] == "equity" and r["weight_pct"] > pp["max_position_pct"]:
            flags.append({"kind": "oversize", "symbols": [r["symbol"]],
                          "detail": f"{r['symbol']} is {r['weight_pct']:.1f}% (cap {pp['max_position_pct']}%)"})
    for name, g in groups.items():
        if g["weight_pct"] > pp["max_group_pct"]:
            flags.append({"kind": "group_concentration", "symbols": g["members"],
                          "detail": f"{name}: {g['weight_pct']:.1f}% across {', '.join(g['members'])} "
                                    f"(cap {pp['max_group_pct']}%)"})
    for s, w in sectors.items():
        if w > pp["max_sector_pct"]:
            flags.append({"kind": "sector_concentration", "detail": f"{s} is {w:.1f}% (cap {pp['max_sector_pct']}%)"})
    deep = [r for r in rows if r["pnl_pct"] <= pp["deep_loss_pct"]]
    if deep:
        flags.append({"kind": "deep_losers", "symbols": [r["symbol"] for r in deep],
                      "detail": f"{len(deep)} positions down {abs(pp['deep_loss_pct']):.0f}%+ from cost: "
                                "re-check the thesis (not a sell signal on its own)"})
    if core_pct < pp["core_target_pct"]:
        flags.append({"kind": "no_core", "detail": f"index core is {core_pct:.1f}% vs {pp['core_target_pct']}% target"})
    for bucket, target in (pp.get("asset_mix_target_pct") or {}).items():
        have = mix.get(bucket, 0.0)
        if abs(have - target) >= 5:
            flags.append({"kind": "asset_mix", "detail": f"{bucket} {have:.1f}% vs {target}% target"})

    winners = sorted((r for r in rows if r["pnl"] > 0), key=lambda r: -r["pnl"])
    losers = sorted((r for r in rows if r["pnl"] < 0), key=lambda r: r["pnl"])
    return {
        "summary": {
            "holdings": n,
            "invested": round(total_invested, 2),
            "value": round(total_value, 2),
            "pnl": round(total_value - total_invested, 2),
            "pnl_pct": round((total_value - total_invested) / total_invested * 100, 2) if total_invested else 0,
            "top5_pct": round(sum(r["weight_pct"] for r in rows[:5]), 2),
            "hhi": round(hhi, 4),
            "effective_holdings": round(1 / hhi, 1) if hhi else 0,
            "core_index_pct": round(core_pct, 2),
            "winners": len(winners),
            "losers": len(losers),
            "loss_from_losers": round(sum(r["pnl"] for r in losers), 2),
            "gain_from_winners": round(sum(r["pnl"] for r in winners), 2),
        },
        "holdings": rows,
        "sectors": dict(sorted(((k, round(v, 2)) for k, v in sectors.items()), key=lambda kv: -kv[1])),
        "asset_mix": {k: round(v, 2) for k, v in sorted(mix.items(), key=lambda kv: -kv[1])},
        "groups": groups,
        "flags": flags,
    }

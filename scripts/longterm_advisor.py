#!/usr/bin/env python3
"""Long-term investment advisor CLI (Indian equities). Proposals only, never orders.

    python scripts/longterm_advisor.py analyze   --holdings investor/holdings.xlsx
    python scripts/longterm_advisor.py tax       --holdings investor/holdings.xlsx [--ytd investor/decisions.yaml]
    python scripts/longterm_advisor.py rebalance --holdings investor/holdings.xlsx --decisions investor/decisions.yaml
    python scripts/longterm_advisor.py report    --holdings investor/holdings.xlsx --decisions investor/decisions.yaml \
                                                 --out investor/report.md
    python scripts/longterm_advisor.py quality   --metrics investor/research/TITAN.yaml
    python scripts/longterm_advisor.py ev        --scenarios investor/research/TITAN.yaml
    python scripts/longterm_advisor.py prices    --holdings investor/holdings.xlsx   # Upstox -> investor/prices.json

Pass --prices investor/prices.json to analyze/tax/rebalance/report to use live
prices instead of the export's previous close.

Add --json to any command for machine-readable output. Personal files live
in investor/ (git-ignored).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrader.longterm import (analyze, load_holdings, load_policy, plan_rebalance,  # noqa: E402
                                 quality_score, scenario_ev, tax_report)
from autotrader.longterm.report import render_analysis, render_rebalance, render_tax  # noqa: E402

DISCLAIMER = ("_Educational tooling, not investment advice. The author of this report is not a "
              "SEBI-registered investment adviser. Verify prices, tax rules and lot-wise costs "
              "before acting; every trade is your own decision._")


def _yaml(path: str | None) -> dict:
    return (yaml.safe_load(Path(path).read_text()) or {}) if path else {}


def _emit(obj, md: str, as_json: bool) -> None:
    print(json.dumps(obj, indent=2, default=str) if as_json else md)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["analyze", "tax", "rebalance", "report", "quality", "ev", "prices"])
    ap.add_argument("--holdings", help="broker holdings export (.xlsx or .csv)")
    ap.add_argument("--policy", help="policy YAML (default config/longterm_policy.yaml)")
    ap.add_argument("--decisions", help="decisions YAML for rebalance/report")
    ap.add_argument("--ytd", help="YAML with realised_ytd {stcg, stcl, ltcg, ltcl} (defaults to --decisions)")
    ap.add_argument("--pnl", help="broker P&L statement for this FY: realised gains replace realised_ytd "
                                  "(holding period from `realised_terms` in --decisions, else short-term)")
    ap.add_argument("--metrics", help="YAML with fundamentals for `quality`")
    ap.add_argument("--scenarios", help="YAML with price/horizon/scenarios for `ev`")
    ap.add_argument("--prices", help="prices JSON from the `prices` command: overrides the export's LTP")
    ap.add_argument("--no-stats", action="store_true", help="`prices`: skip 52-week stats (one call per symbol)")
    ap.add_argument("--out", help="write the markdown here as well as printing it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    policy = load_policy(args.policy)

    if args.command in ("quality", "ev"):
        src = _yaml(args.metrics if args.command == "quality" else args.scenarios)
        if args.command == "quality":
            res = quality_score(src.get("metrics", src), financial=bool(src.get("financial")))
            md = f"Quality score: {res['score']} ({res['verdict']})\n" + "\n".join(
                f"- {k}: {v['value']} ({v['grade']})" for k, v in res["detail"].items())
            if res["red_flags"]:
                md += "\nRed flags: " + "; ".join(res["red_flags"])
            if res["missing"]:
                md += "\nMissing: " + ", ".join(res["missing"])
        else:
            res = scenario_ev(float(src["price"]), src["scenarios"], float(src.get("horizon_years", 5)),
                              float(src.get("hurdle_cagr_pct", 11)), float(src.get("dividend_yield_pct", 0)))
            md = "\n".join([f"{r['name']:>5} p={r['prob']}: {r['target']:.0f} ({r['return_pct']:+.0f}%, "
                            f"{r['cagr_pct']:+.1f}%/yr)" for r in res["scenarios"]]
                           + [f"expected {res['expected_price']:.0f} = {res['expected_cagr_pct']:+.1f}%/yr vs "
                              f"hurdle {res['hurdle_cagr_pct']}%: {res['verdict']}"])
        _emit(res, md, args.json)
        return 0

    if not args.holdings:
        ap.error("--holdings is required")
    holdings = load_holdings(args.holdings)
    decisions = _yaml(args.decisions)

    if args.command == "prices":
        from autotrader.longterm.market import INDEX_KEYS, refresh
        pp = policy["portfolio_policy"]
        symbols = {h.symbol: h.isin for h in holdings}
        for c in (pp.get("core_instruments") or []) + (pp.get("core_stocks") or []):
            symbols.setdefault(c["symbol"], c.get("isin", ""))
        for sym in (decisions.get("decisions") or {}):
            symbols.setdefault(sym.upper(), "")
        symbols.update({name: "" for name in INDEX_KEYS})
        out = Path(args.out or "investor/prices.json")
        try:
            res = refresh(symbols, out, with_stats=not args.no_stats)
        except (OSError, RuntimeError) as e:  # URLError is an OSError
            print(f"Upstox unreachable ({e}). If this runs in a sandbox, allow api.upstox.com and "
                  "assets.upstox.com in its network settings.", file=sys.stderr)
            return 2
        md = f"{len(res['prices'])} prices from Upstox at {res['as_of']} -> {out}"
        if res["unresolved"]:
            md += f"\nNo instrument key for: {', '.join(res['unresolved'])}"
        _emit(res, md, args.json)
        return 0

    if args.prices:
        live = json.loads(Path(args.prices).read_text())["prices"]
        for h in holdings:
            h.ltp = live.get(h.symbol, h.ltp)
        decisions.setdefault("prices", {})
        decisions["prices"] = {**live, **(decisions["prices"] or {})}
    ytd = _yaml(args.ytd).get("realised_ytd") if args.ytd else decisions.get("realised_ytd")
    pnl_note = ""
    if args.pnl:
        from autotrader.longterm.pnl import load_realised
        realised = load_realised(args.pnl, decisions.get("realised_terms"))
        ytd = decisions["realised_ytd"] = realised["realised_ytd"]
        pnl_note = ("Realised this FY (from the P&L statement): "
                    + ", ".join(f"{x['symbol']} {x['realised']:+,.0f} ({x['term']}-term)" for x in realised["lines"]))
        if realised["assumed_short_term"]:
            pnl_note += (f". Holding period not in the statement, assumed short-term: "
                         f"{', '.join(realised['assumed_short_term'])} (set `realised_terms` to correct)")

    if args.command == "analyze":
        res = analyze(holdings, policy)
        md = render_analysis(res)
    elif args.command == "tax":
        res = tax_report(holdings, policy, ytd)
        md = render_tax(res) + (f"\n\n{pnl_note}" if pnl_note else "")
    elif args.command == "rebalance":
        if not args.decisions:
            ap.error("--decisions is required for rebalance")
        res = plan_rebalance(holdings, policy, decisions)
        md = render_rebalance(res)
    else:
        res = {"analysis": analyze(holdings, policy), "tax": tax_report(holdings, policy, ytd)}
        parts = [f"# Long-term portfolio review — {datetime.date.today():%d %b %Y}", "", DISCLAIMER, "",
                 render_analysis(res["analysis"]), "", render_tax(res["tax"])]
        if pnl_note:
            parts += ["", pnl_note]
        if args.decisions:
            res["rebalance"] = plan_rebalance(holdings, policy, decisions)
            parts += ["", render_rebalance(res["rebalance"])]
        md = "\n".join(parts) + "\n"

    if args.out and args.command != "prices":
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md)
    _emit(res, md, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

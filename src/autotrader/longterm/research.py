"""Stock research helpers: a fundamentals quality score and a scenario (EV) check.

Both are deterministic arithmetic over numbers the user (or Claude, from
screener.in / annual reports) supplies. Nothing here fetches data or guesses.
"""
from __future__ import annotations

# metric -> (good, ok, higher_is_better, weight). Thresholds are deliberately
# plain-vanilla Indian long-term-investing rules of thumb; edit them in one place.
QUALITY_RULES = {
    "roce_pct":            (18, 12, True, 15),
    "roe_pct":             (15, 10, True, 10),
    "sales_cagr_5y_pct":   (12, 8, True, 10),
    "profit_cagr_5y_pct":  (15, 8, True, 10),
    "ocf_to_pat":          (0.9, 0.7, True, 10),   # cash conversion, 5y cumulative
    "debt_to_equity":      (0.3, 1.0, False, 10),  # skip for banks/NBFCs
    "promoter_holding_pct": (50, 35, True, 5),
    "promoter_pledge_pct": (0, 5, False, 10),
    "pe_vs_5y_median":     (1.0, 1.3, False, 10),  # current PE / its own 5y median
    "peg":                 (1.2, 2.0, False, 10),
}
FINANCIALS_SKIP = {"debt_to_equity", "ocf_to_pat", "roce_pct"}
FINANCIAL_EXTRA = {
    "gnpa_pct": (2.5, 5.0, False, 10),
    "car_pct":  (16, 13, True, 10),
    "roa_pct":  (1.5, 0.9, True, 10),
}


def _grade(v: float, good: float, ok: float, higher: bool) -> float:
    if higher:
        return 1.0 if v >= good else 0.5 if v >= ok else 0.0
    return 1.0 if v <= good else 0.5 if v <= ok else 0.0


def quality_score(metrics: dict, financial: bool = False) -> dict:
    rules = {k: v for k, v in QUALITY_RULES.items() if not (financial and k in FINANCIALS_SKIP)}
    if financial:
        rules.update(FINANCIAL_EXTRA)
    got, possible, detail, missing, red_flags = 0.0, 0.0, {}, [], []
    for key, (good, ok, higher, weight) in rules.items():
        v = metrics.get(key)
        if v is None:
            missing.append(key)
            continue
        g = _grade(float(v), good, ok, higher)
        got += g * weight
        possible += weight
        detail[key] = {"value": v, "grade": {1.0: "good", 0.5: "ok", 0.0: "weak"}[g]}
    if (metrics.get("promoter_pledge_pct") or 0) > 10:
        red_flags.append("promoter pledge above 10%")
    if metrics.get("ocf_to_pat") is not None and not financial and metrics["ocf_to_pat"] < 0.5:
        red_flags.append("profits are not turning into cash (OCF/PAT < 0.5)")
    if metrics.get("profit_cagr_5y_pct") is not None and metrics["profit_cagr_5y_pct"] < 0:
        red_flags.append("profits shrank over 5 years")
    if not financial and (metrics.get("debt_to_equity") or 0) > 2:
        red_flags.append("debt/equity above 2")
    score = round(got / possible * 100) if possible else None
    verdict = ("insufficient data" if score is None or len(missing) > len(rules) / 2 else
               "compounder-grade" if score >= 75 and not red_flags else
               "decent, watch valuation" if score >= 55 else
               "weak: needs a special reason to own")
    return {"score": score, "verdict": verdict, "detail": detail, "missing": missing, "red_flags": red_flags}


def scenario_ev(price: float, scenarios: list[dict], horizon_years: float,
                hurdle_cagr_pct: float = 11.0, dividend_yield_pct: float = 0.0) -> dict:
    """scenarios: [{"name": "bull", "prob": 0.25, "target": 900}, ...] at the horizon.

    Compares the probability-weighted CAGR to a hurdle: what a Nifty index fund
    might reasonably do over the same years (default 11%, an assumption, not a forecast).
    """
    total_p = sum(float(s["prob"]) for s in scenarios)
    if abs(total_p - 1.0) > 0.01:
        raise ValueError(f"probabilities add up to {total_p:.2f}, not 1")
    rows, ev_price = [], 0.0
    for s in scenarios:
        t = float(s["target"])
        cagr = ((t / price) ** (1 / horizon_years) - 1) * 100 + dividend_yield_pct
        ev_price += float(s["prob"]) * t
        rows.append({"name": s["name"], "prob": s["prob"], "target": t,
                     "return_pct": round((t / price - 1) * 100, 1), "cagr_pct": round(cagr, 1)})
    ev_cagr = ((ev_price / price) ** (1 / horizon_years) - 1) * 100 + dividend_yield_pct
    worst = min(rows, key=lambda r: r["target"])
    return {
        "price": price,
        "horizon_years": horizon_years,
        "scenarios": rows,
        "expected_price": round(ev_price, 2),
        "expected_cagr_pct": round(ev_cagr, 1),
        "hurdle_cagr_pct": hurdle_cagr_pct,
        "excess_vs_hurdle_pct": round(ev_cagr - hurdle_cagr_pct, 1),
        "worst_case_return_pct": worst["return_pct"],
        "verdict": ("beats the index hurdle" if ev_cagr >= hurdle_cagr_pct + 3 else
                    "roughly index-like: an index fund does this with less risk" if ev_cagr >= hurdle_cagr_pct - 2 else
                    "below the hurdle: the index is the better bet on these numbers"),
    }

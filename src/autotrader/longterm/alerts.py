"""Daily alerts for the long-term plan: price levels, upcoming results, big moves.

Reads the personal decisions file:

    watchlist_buy_below: {SUNPHARMA: 1720, ...}        # not held: buy when at or below
    guards: {HDFCBANK: {add_below: 850, trim_above: 1400}, ...}   # held: from each thesis
    events: {TMPV: 2026-11-13, ...}                     # results / decision dates

and a prices file from the `prices` command. Optionally compares with an
earlier prices file to flag big moves. Nothing here trades; it tells you
what to look at.
"""
from __future__ import annotations

import datetime
from pathlib import Path


def build_alerts(decisions: dict, prices: dict, previous: dict | None = None, held: set[str] | None = None,
                 today: datetime.date | None = None, event_days: int = 7, move_pct: float = 8.0) -> list[dict]:
    today = today or datetime.date.today()
    held = held or set()
    out = []
    for sym, level in (decisions.get("watchlist_buy_below") or {}).items():
        px = prices.get(sym)
        if px is not None and px <= float(level) and sym not in held:
            out.append({"kind": "buy_zone", "symbol": sym, "price": px, "level": float(level),
                        "text": f"{sym} at ₹{px:,.2f} is at or below your buy level ₹{float(level):,.0f}"})
    for sym, g in (decisions.get("guards") or {}).items():
        px = prices.get(sym)
        if px is None:
            continue
        if g.get("add_below") is not None and px <= float(g["add_below"]):
            out.append({"kind": "add_zone", "symbol": sym, "price": px, "level": float(g["add_below"]),
                        "text": f"{sym} at ₹{px:,.2f} is in the ADD zone (below ₹{float(g['add_below']):,.0f})"})
        if g.get("trim_above") is not None and px >= float(g["trim_above"]):
            out.append({"kind": "trim_zone", "symbol": sym, "price": px, "level": float(g["trim_above"]),
                        "text": f"{sym} at ₹{px:,.2f} is in the TRIM zone (above ₹{float(g['trim_above']):,.0f})"})
    for sym, when in (decisions.get("events") or {}).items():
        d = when if isinstance(when, datetime.date) else datetime.date.fromisoformat(str(when))
        days = (d - today).days
        if 0 <= days <= event_days:
            out.append({"kind": "event", "symbol": sym, "date": d.isoformat(),
                        "text": f"{sym}: results / decision date {d:%a %d %b}"
                                + (" (today)" if days == 0 else f" (in {days} day{'s' if days != 1 else ''})")})
    if previous:
        exiting = {k.upper() for k, v in (decisions.get("decisions") or {}).items() if (v or {}).get("action") == "exit"}
        for sym, px in prices.items():
            old = previous.get(sym)
            if old and sym not in exiting and (sym in held or sym in (decisions.get("watchlist_buy_below") or {})):
                chg = (px / old - 1) * 100
                if abs(chg) >= move_pct:
                    out.append({"kind": "big_move", "symbol": sym, "price": px, "change_pct": round(chg, 1),
                                "text": f"{sym} moved {chg:+.1f}% to ₹{px:,.2f} since the last check: re-read its thesis"})
    return out


def log_to_theses(alerts: list[dict], theses_dir: str | Path, today: datetime.date | None = None) -> list[str]:
    """Append one review-log row per alert to investor/theses/<SYMBOL>.md (if the thesis exists)."""
    today = today or datetime.date.today()
    touched = []
    for a in alerts:
        path = Path(theses_dir) / f"{a['symbol']}.md"
        if not path.exists():
            continue
        row = f"| {today.isoformat()} | auto-alert: {a['text']} | check | review |"
        text = path.read_text()
        if row in text:
            continue  # idempotent: the same alert is logged once a day
        path.write_text(text.rstrip("\n") + "\n" + row + "\n")
        touched.append(a["symbol"])
    return touched


def render_alerts(alerts: list[dict], as_of: str) -> str:
    if not alerts:
        return f"No alerts ({as_of}): nothing has reached a buy, add or trim level, and no results are due this week."
    order = {"trim_zone": 0, "buy_zone": 1, "add_zone": 2, "event": 3, "big_move": 4}
    lines = [f"Alerts ({as_of}):"]
    lines += [f"- {a['text']}" for a in sorted(alerts, key=lambda a: order[a["kind"]])]
    return "\n".join(lines)

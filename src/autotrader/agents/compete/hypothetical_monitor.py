"""Compete Hypothetical Monitor — intraday price checks for all compete stacks.

Runs each intraday iteration. Fetches live price for each stack's pick and
notifies via Slack when a hypothetical stop or target is crossed.
Uses stop_hit / target1_hit / target2_hit flags to avoid duplicate notifications.
"""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState
from autotrader.tools.notifications import get_notifier

logger = logging.getLogger(__name__)
AGENT_NAME = "CompeteHypotheticalMonitor"


def _fetch_price(symbol: str) -> float | None:
    try:
        import yfinance as yf
        t = yf.Ticker(f"{symbol}.NS")
        hist = t.history(period="1d", interval="1m")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("[%s] Price fetch failed for %s: %s", AGENT_NAME, symbol, exc)
        return None


def compete_hypothetical_monitor_agent(state: TradingState) -> dict[str, Any]:
    cfg = load_config()
    if not cfg.compete.enabled:
        return {}

    results = list(state.get("competitor_results", []))
    if not results:
        return {}

    notifier = get_notifier(cfg.notifications)
    updated: list[dict] = []
    price_cache: dict[str, float | None] = {}

    for r in results:
        pick = r.get("pick")
        if not pick or not r.get("pass_review", True):
            updated.append(r)
            continue

        if pick not in price_cache:
            price_cache[pick] = _fetch_price(pick)

        price = price_cache[pick]
        if price is None:
            updated.append(r)
            continue

        entry   = r.get("entry_price") or 0
        stop    = r.get("hypothetical_stop")
        tgt1    = r.get("hypothetical_target1")
        tgt2    = r.get("hypothetical_target2")
        name    = r.get("name", "?")
        pnl_pct = round((price - entry) / entry * 100, 2) if entry else 0

        r = dict(r)  # copy

        if stop and price <= stop and not r.get("stop_hit"):
            r["stop_hit"] = True
            notifier.send(
                f"🔴 [COMPETE HYPO] {name} — {pick} STOP HIT",
                f"Entry: ₹{entry:.2f}  |  Price: ₹{price:.2f}  |  P&L: {pnl_pct:+.2f}%\n"
                f"Stop: ₹{stop:.2f}",
            )
            logger.info("[%s] %s stop hit: %s @ %.2f (pnl %.2f%%)", AGENT_NAME, name, pick, price, pnl_pct)

        elif tgt2 and price >= tgt2 and not r.get("target2_hit"):
            r["target2_hit"] = True
            notifier.send(
                f"🟢 [COMPETE HYPO] {name} — {pick} TARGET 2 HIT",
                f"Entry: ₹{entry:.2f}  |  Price: ₹{price:.2f}  |  P&L: {pnl_pct:+.2f}%\n"
                f"Target 2: ₹{tgt2:.2f}",
            )
            logger.info("[%s] %s target2 hit: %s @ %.2f (pnl %.2f%%)", AGENT_NAME, name, pick, price, pnl_pct)

        elif tgt1 and price >= tgt1 and not r.get("target1_hit"):
            r["target1_hit"] = True
            notifier.send(
                f"🟡 [COMPETE HYPO] {name} — {pick} TARGET 1 HIT",
                f"Entry: ₹{entry:.2f}  |  Price: ₹{price:.2f}  |  P&L: {pnl_pct:+.2f}%\n"
                f"Target 1: ₹{tgt1:.2f}  |  Target 2: ₹{tgt2:.2f}",
            )
            logger.info("[%s] %s target1 hit: %s @ %.2f (pnl %.2f%%)", AGENT_NAME, name, pick, price, pnl_pct)

        updated.append(r)

    entry_log = audit_entry(agent=AGENT_NAME, action="checked", data={"stacks": len(updated)})
    return {"competitor_results": updated, "audit_trail": [entry_log]}

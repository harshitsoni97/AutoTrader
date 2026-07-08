"""Intraday Hunt Agent — catch a day that opens weak but turns favorable, and
redeploy freed capital after a trade closes out.

Two situations it covers, both long-only:

  1. Weak open, better later. Pre-market builds plans only when conviction clears
     the floor. On a morning below the floor (or in a blocked regime) no plans are
     made. If the regime IMPROVES intraday — confidence crosses the floor and the
     regime turns tradeable — this agent builds plans from the pre-market shortlist
     (scored_opportunities), sized by the CURRENT (improved) confidence.

  2. Trade closed, capital freed. If a booked position has hit its target OR its
     stop and exited, the slot and its capital are free again. When a slot, daily
     trade budget, AND freed capital all remain — and there's still a confident,
     not-yet-traded opportunity — hunt one more.

Lean by design: it reuses the pre-market shortlist and the existing
trade_construction sizing rather than re-running the full scan/LLM pipeline. It
never re-books a symbol already traded today (no churning a name that just
stopped out), never exceeds the concurrent-position or daily-trade caps, and
sizes strictly within the capital that is actually free.
"""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "IntradayHuntAgent"

# Long-only hunting fires only in clearly long-favorable regimes. This is
# stricter than blocked_regimes (which lists only extreme-bear labels) so a
# confidently *bearish* day can't trigger longs.
_LONG_FAVORABLE = {"bullish", "risk_on", "cautiously_bullish"}


def intraday_hunt_agent(state: TradingState) -> dict[str, Any]:
    positions = state.get("positions", []) or []
    open_positions = [p for p in positions if p.get("status") == "OPEN"]
    # Every symbol touched today — open OR already closed — is off-limits so we
    # never re-book a name that just hit its stop/target (no churn).
    traded_symbols = {p.get("symbol") for p in positions if p.get("symbol")}

    cfg = load_config()
    policy = cfg.trading_policy

    # An unbooked pre-market plan is still pending → let EntryAgent book it first;
    # don't hunt on top of it this cycle.
    pending_plans = [
        p for p in state.get("trade_plans", []) or []
        if p.get("symbol") and p["symbol"] not in traded_symbols
    ]
    if pending_plans:
        return {}

    # Respect the hard caps.
    max_concurrent = getattr(policy, "max_concurrent_positions", 3)
    if len(open_positions) >= max_concurrent:
        return {}
    daily_trades = state.get("daily_trades_taken", 0)
    max_daily = getattr(policy, "max_daily_trades", 6)
    if daily_trades >= max_daily:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="daily_limit_reached",
                                            data={"daily_trades": daily_trades, "max": max_daily})]}

    scored = state.get("scored_opportunities", [])
    fresh = [s for s in scored if s.get("symbol") and s["symbol"] not in traded_symbols]
    if not fresh:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_fresh_opportunities",
                                            data={"traded": sorted(traded_symbols)})]}

    regime = state.get("market_regime", "unknown")
    confidence = state.get("market_confidence", 0.0)
    floor = getattr(policy, "confidence_min_trade", 0.65)

    # Only hunt when the (refreshed intraday) regime is explicitly long-favorable.
    if regime not in _LONG_FAVORABLE:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="regime_not_long_favorable",
                                            data={"regime": regime})]}
    if confidence < floor:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="confidence_below_floor",
                                            data={"confidence": confidence, "floor": floor})]}

    # Capital tied up in still-open positions is NOT available; only the rest is.
    total_capital = policy.total_capital
    deployed = sum(
        (p.get("qty", 0) or 0) * (p.get("entry_price") or p.get("entry") or 0)
        for p in open_positions
    )
    available = total_capital - deployed
    # Not worth opening a fragment of a position; require a meaningful slug of cash.
    min_deployable = total_capital * getattr(policy, "max_capital_per_trade_pct", 50) / 100 * 0.5
    if available < min_deployable:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="insufficient_free_capital",
                                            data={"available": round(available), "deployed": round(deployed)})]}

    # Build plans from the not-yet-traded shortlist, sized by current confidence.
    # Pass a shallow copy with the filtered shortlist so trade_construction ranks
    # only fresh names; its slot math already keys off OPEN positions.
    from autotrader.agents.layer5.trade_construction import trade_construction_agent
    hunt_state = dict(state)
    hunt_state["scored_opportunities"] = fresh
    tc = trade_construction_agent(hunt_state)
    plans = tc.get("trade_plans", [])
    if not plans:
        return {"audit_trail": tc.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="no_plans_after_construction", data={})]}

    # Cap total new deployment to the capital that is actually free (trade
    # construction sizes off total_capital and doesn't know about open positions).
    plans = _cap_to_available(plans, available)
    if not plans:
        return {"audit_trail": tc.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="capped_out_no_capital",
                        data={"available": round(available)})]}

    logger.info("[%s] Hunting %d plan(s) — regime %s (%.0f%%), free capital ₹%.0f",
                AGENT_NAME, len(plans), regime, confidence * 100, available)
    return {
        "trade_plan": plans[0],
        "trade_plans": plans,
        "messages": tc.get("messages", []),
        "audit_trail": tc.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="intraday_hunt_built_plans",
                        data={"regime": regime, "confidence": confidence,
                              "available": round(available),
                              "symbols": [p["symbol"] for p in plans]})],
    }


def _cap_to_available(plans: list[dict], available: float) -> list[dict]:
    """Trim plans so their combined notional fits the free capital.

    Fill highest-score plans first; shrink the last one to fit, drop plans that
    can't afford a single share. Keeps the multi-plan book honest when only part
    of the capital is free after a close.
    """
    kept: list[dict] = []
    remaining = available
    for p in sorted(plans, key=lambda x: x.get("score", 0), reverse=True):
        entry = p.get("entry", 0) or 0
        if entry <= 0:
            continue
        want = p.get("qty", 0) or 0
        affordable = int(remaining // entry)
        qty = min(want, affordable)
        if qty < 1:
            continue
        if qty != want:
            p["qty"] = qty
            p["position_size_inr"] = round(qty * entry, 2)
            p["risk_inr"] = round(qty * (entry - p.get("stop", entry)), 2)
            p["reward_inr"] = round(qty * (p.get("target1", entry) - entry), 2)
        remaining -= qty * entry
        kept.append(p)
    return kept

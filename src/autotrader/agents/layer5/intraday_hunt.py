"""Intraday Hunt Agent — catch a day that opens weak but turns favorable.

Pre-market builds plans only when conviction clears the floor. On a morning that's
below the floor (or in a blocked regime), no plans are made. If the regime then
IMPROVES intraday — confidence crosses the floor and the regime is tradeable —
this agent builds plans from the pre-market shortlist (scored_opportunities),
sized by the CURRENT (improved) confidence, so the EntryAgent can book them at
live prices.

Lean by design: it reuses the pre-market shortlist and the existing
trade_construction sizing rather than re-running the full scan/LLM pipeline. It
only fires when there are no plans and nothing booked yet, so a bearish day stays
flat and it never double-books.
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
    # Already have plans (from pre-market) → nothing to hunt; EntryAgent handles them.
    if state.get("trade_plans"):
        return {}
    # Something already booked / open today → don't hunt again.
    if state.get("daily_trades_taken", 0) > 0:
        return {}
    if any(p.get("status") == "OPEN" for p in state.get("positions", [])):
        return {}

    scored = state.get("scored_opportunities", [])
    if not scored:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="no_opportunities", data={})]}

    cfg = load_config()
    policy = cfg.trading_policy
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

    # Regime improved intraday → build plans from the pre-market shortlist, sized
    # by the current confidence. EntryAgent (next node) books them at live prices.
    from autotrader.agents.layer5.trade_construction import trade_construction_agent
    tc = trade_construction_agent(state)
    plans = tc.get("trade_plans", [])
    if not plans:
        return {"audit_trail": tc.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="no_plans_after_construction", data={})]}

    logger.info("[%s] Regime improved to %s (%.0f%%) — built %d plan(s) intraday",
                AGENT_NAME, regime, confidence * 100, len(plans))
    return {
        "trade_plan": tc.get("trade_plan", {}),
        "trade_plans": plans,
        "messages": tc.get("messages", []),
        "audit_trail": tc.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="intraday_hunt_built_plans",
                        data={"regime": regime, "confidence": confidence,
                              "symbols": [p["symbol"] for p in plans]})],
    }

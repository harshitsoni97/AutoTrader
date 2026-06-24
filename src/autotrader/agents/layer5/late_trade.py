"""Intraday Late-Trade Agent.

Fires during market hours when pre-market placed 0 trades (often because regime
was mis-classified) but the refreshed intraday regime is now tradeable.

Picks from the pre-market `scored_opportunities` list — avoids re-running the
entire pre-market pipeline intraday. Uses live LTP from Upstox to confirm
entry price before placing.
"""

from __future__ import annotations

import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "LateTradeTriggerAgent"

_BLOCKED_REGIMES = {"high_volatility_bear", "risk_off_extreme", "risk_off"}


def late_trade_agent(state: TradingState) -> dict[str, Any]:
    """Execute pre-market top opportunities if regime improved intraday."""
    cfg = load_config()
    policy = cfg.trading_policy

    # Only fire if no trades have been placed yet today
    daily_trades = state.get("daily_trades_taken", 0)
    if daily_trades > 0:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="skipped_trades_exist",
                                            data={"daily_trades": daily_trades})]}

    # Only fire if we have pre-market opportunities waiting
    scored = state.get("scored_opportunities", [])
    if not scored:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="skipped_no_opportunities", data={})]}

    # Check current intraday regime (already updated by market_regime_agent this cycle)
    regime = state.get("market_regime", "unknown")
    confidence = state.get("market_confidence", 0.0)

    if regime in _BLOCKED_REGIMES:
        logger.info("[%s] Regime still blocked (%s) — no late trade", AGENT_NAME, regime)
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="regime_still_blocked",
                                            data={"regime": regime})]}

    if confidence < policy.minimum_confidence:
        logger.info("[%s] Confidence too low (%.2f < %.2f) — no late trade", AGENT_NAME, confidence, policy.minimum_confidence)
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="confidence_too_low",
                                            data={"confidence": confidence, "threshold": policy.minimum_confidence})]}

    # Regime is now tradeable — execute top opportunity via trade_construction + execution
    logger.info("[%s] Regime flipped to %s (%.0f%%) — triggering late trade", AGENT_NAME, regime, confidence * 100)

    from autotrader.agents.layer5.trade_construction import trade_construction_agent
    from autotrader.agents.layer5.execution import execution_agent

    tc_result = trade_construction_agent(state)
    if not tc_result.get("trade_plans"):
        return {
            "audit_trail": tc_result.get("audit_trail", []) + [
                audit_entry(agent=AGENT_NAME, action="no_valid_plans", data={})
            ]
        }

    # Merge tc results into a mini-state for execution
    exec_state = {**state, **tc_result}
    exec_result = execution_agent(exec_state)

    trades_placed = len(exec_result.get("orders", []))
    logger.info("[%s] Late trade: placed %d order(s) after regime flip to %s", AGENT_NAME, trades_placed, regime)

    return {
        "trade_plan": tc_result.get("trade_plan", {}),
        "trade_plans": tc_result.get("trade_plans", []),
        "orders": exec_result.get("orders", []),
        "positions": exec_result.get("positions", state.get("positions", [])),
        "daily_trades_taken": exec_result.get("daily_trades_taken", daily_trades),
        "messages": tc_result.get("messages", []) + exec_result.get("messages", []),
        "audit_trail": tc_result.get("audit_trail", []) + exec_result.get("audit_trail", []) + [
            audit_entry(agent=AGENT_NAME, action="late_trade_executed", data={
                "regime": regime,
                "confidence": confidence,
                "trades_placed": trades_placed,
                "symbols": [p["symbol"] for p in exec_result.get("orders", [])],
            })
        ],
    }

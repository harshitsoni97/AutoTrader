"""Governance Agent — enforces all platform trading policies."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = logging.getLogger(__name__)

AGENT_NAME = "GovernanceAgent"


def governance_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Running governance checks", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    scored = state.get("scored_opportunities", [])

    def reject(reason: str) -> dict[str, Any]:
        msg = create_message(
            source=AGENT_NAME, target="RiskAgent",
            payload={"approved": False, "reason": reason},
        )
        entry = audit_entry(agent=AGENT_NAME, action="governance_rejected", data={"reason": reason})
        logger.warning("[%s] REJECTED: %s", AGENT_NAME, reason)
        return {
            "governance_approved": False,
            "governance_reason": reason,
            "messages": [msg],
            "audit_trail": [entry],
        }

    # 1. Platform enabled
    if not policy.enabled:
        return reject("Trading platform is disabled")

    # 2. No eligible opportunities
    if not scored:
        return reject("No opportunities meet minimum score threshold")

    # 3. Daily trade limit
    daily_trades = state.get("daily_trades_taken", 0)
    if daily_trades >= policy.max_daily_trades:
        return reject(f"Daily trade limit reached ({daily_trades}/{policy.max_daily_trades})")

    # 4. Concurrent position limit
    positions = state.get("positions", [])
    if len(positions) >= policy.max_concurrent_positions:
        return reject(f"Max concurrent positions reached ({len(positions)}/{policy.max_concurrent_positions})")

    # 5. Daily loss limit
    daily_pnl = state.get("daily_pnl", 0.0)
    capital = policy.total_capital
    daily_loss_pct = (-daily_pnl / capital * 100) if daily_pnl < 0 else 0.0
    if daily_loss_pct >= policy.max_daily_loss_pct:
        return reject(f"Daily loss limit hit ({daily_loss_pct:.2f}% >= {policy.max_daily_loss_pct}%)")

    # 6. Consecutive losses stop
    consecutive_losses = state.get("consecutive_losses", 0)
    if consecutive_losses >= policy.stop_trading_after_losses:
        return reject(f"Stopped after {consecutive_losses} consecutive losses")

    # 7. Market regime check
    regime = state.get("market_regime", "unknown")
    if regime in policy.blocked_regimes:
        return reject(f"Market regime '{regime}' is blocked by policy")

    # 8. Confidence threshold
    confidence = state.get("market_confidence", 0.0)
    if confidence < policy.minimum_confidence:
        return reject(f"Market confidence too low ({confidence:.2f} < {policy.minimum_confidence})")

    # 9. No-reentry check: remove already-held symbols from the eligible list
    if not policy.allow_reentry_same_stock:
        existing_symbols = {p.get("symbol") for p in positions}
        order_symbols = {o.get("symbol") for o in state.get("orders", [])}
        blocked = existing_symbols | order_symbols
        scored = [s for s in scored if s["symbol"] not in blocked]
        if not scored:
            return reject("No eligible symbols after filtering already-held positions")

    reason = f"All governance checks passed — {len(scored)} eligible opportunity(s)"
    msg = create_message(
        source=AGENT_NAME, target="RiskAgent",
        payload={"approved": True, "reason": reason, "top_symbol": scored[0]["symbol"] if scored else ""},
    )
    entry = audit_entry(agent=AGENT_NAME, action="governance_approved", data={
        "reason": reason,
        "daily_trades": daily_trades,
        "positions": len(positions),
        "daily_pnl": daily_pnl,
        "consecutive_losses": consecutive_losses,
        "regime": regime,
        "confidence": confidence,
    })
    logger.info("[%s] APPROVED: %s", AGENT_NAME, reason)

    return {
        "governance_approved": True,
        "governance_reason": reason,
        "messages": [msg],
        "audit_trail": [entry],
    }

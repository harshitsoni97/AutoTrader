"""Trade Construction Agent — calculates entry, stop, targets, and position size."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = logging.getLogger(__name__)

AGENT_NAME = "TradeConstructionAgent"


def trade_construction_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Constructing trade plan", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    scored = state.get("scored_opportunities", [])

    if not scored:
        entry = audit_entry(agent=AGENT_NAME, action="no_opportunity", data={})
        return {"trade_plan": {}, "audit_trail": [entry]}

    top = scored[0]
    symbol = top["symbol"]
    current_price = top.get("current_price", 0)
    atr = top.get("atr", current_price * 0.015)
    pattern = top.get("pattern", "NONE")
    vwap = top.get("vwap", current_price)

    # Entry price logic
    if pattern in ("ORB", "BREAKOUT"):
        entry_price = current_price  # Buy at market/close above trigger
    elif pattern == "VWAP_CROSS":
        entry_price = max(current_price, vwap)  # Entry above VWAP
    else:
        entry_price = current_price

    # Stop loss: 1.5x ATR below entry
    stop_distance = atr * 1.5
    stop_price = round(entry_price - stop_distance, 2)

    # Targets: 2R and 3R
    target1 = round(entry_price + stop_distance * policy.min_risk_reward, 2)
    target2 = round(entry_price + stop_distance * 3.0, 2)

    # Position sizing: risk INR = capital * max_risk_per_trade_pct / 100
    risk_per_trade = policy.total_capital * policy.max_risk_per_trade_pct / 100
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        risk_per_share = atr

    qty = int(risk_per_trade / risk_per_share)
    # Cap by max capital per trade
    max_capital = policy.total_capital * policy.max_capital_per_trade_pct / 100
    qty = min(qty, int(max_capital / entry_price))
    qty = max(qty, 1)

    rr = (target1 - entry_price) / (entry_price - stop_price) if (entry_price - stop_price) > 0 else 0

    trade_plan = {
        "symbol": symbol,
        "entry": round(entry_price, 2),
        "stop": stop_price,
        "target1": target1,
        "target2": target2,
        "qty": qty,
        "position_size_inr": round(qty * entry_price, 2),
        "risk_inr": round(qty * risk_per_share, 2),
        "reward_inr": round(qty * (target1 - entry_price), 2),
        "rr": round(rr, 2),
        "pattern": pattern,
        "score": top.get("score", 0),
        "catalyst_reason": top.get("catalyst_reason", ""),
    }

    msg = create_message(
        source=AGENT_NAME, target="ExecutionAgent",
        symbol=symbol,
        payload=trade_plan,
    )
    entry_audit = audit_entry(agent=AGENT_NAME, action="trade_constructed", data=trade_plan)

    logger.info(
        "[%s] Trade plan: %s Entry=%.2f Stop=%.2f T1=%.2f T2=%.2f Qty=%d R/R=%.2f",
        AGENT_NAME, symbol, entry_price, stop_price, target1, target2, qty, rr,
    )

    return {
        "trade_plan": trade_plan,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }

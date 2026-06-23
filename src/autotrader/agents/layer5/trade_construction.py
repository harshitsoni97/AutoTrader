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

    import math

    top = scored[0]
    symbol = top["symbol"]
    current_price = top.get("current_price", 0) or 0
    if not current_price or math.isnan(current_price) or current_price <= 0:
        entry = audit_entry(agent=AGENT_NAME, action="no_valid_price", data={"symbol": symbol})
        logger.warning("[%s] Skipping %s — current_price is 0 or NaN", AGENT_NAME, symbol)
        return {"trade_plan": {}, "audit_trail": [entry]}

    raw_atr = top.get("atr", None)
    atr = raw_atr if (raw_atr and not math.isnan(raw_atr) and raw_atr > 0) else current_price * 0.015
    pattern = top.get("pattern", "NONE")
    vwap = top.get("vwap", current_price) or current_price

    # Entry price logic
    if pattern in ("ORB", "BREAKOUT"):
        entry_price = current_price  # Buy at market/close above trigger
    elif pattern == "VWAP_CROSS":
        entry_price = max(current_price, vwap)  # Entry above VWAP
    else:
        entry_price = current_price

    # Stop loss: ORB uses ORB low; other patterns use 1.0x ATR below entry
    orb_low = top.get("orb_low", None)
    if pattern == "ORB" and orb_low and orb_low > 0 and orb_low < entry_price:
        stop_price = round(orb_low, 2)
        stop_distance = entry_price - stop_price
    else:
        stop_distance = atr * 1.0
        stop_price = round(entry_price - stop_distance, 2)

    # Target 1: 1R (1x stop distance) — realistic intraday move
    # Target 2: 2R (2x stop distance) — stretch target if momentum holds
    target1 = round(entry_price + stop_distance * 1.0, 2)
    target2 = round(entry_price + stop_distance * policy.min_risk_reward, 2)

    # Position sizing
    risk_per_share = entry_price - stop_price
    if not risk_per_share or math.isnan(risk_per_share) or risk_per_share <= 0:
        risk_per_share = atr if atr > 0 else entry_price * 0.015

    kelly_fraction = state.get("kelly_fraction", 0.0)
    if kelly_fraction > 0:
        # Kelly-derived sizing: allocate kelly_fraction of total capital
        kelly_capital = policy.total_capital * kelly_fraction
        qty = int(kelly_capital / entry_price)
    else:
        # Fixed-fraction fallback: risk max_risk_per_trade_pct of capital
        risk_per_trade = policy.total_capital * policy.max_risk_per_trade_pct / 100
        qty = int(risk_per_trade / risk_per_share)

    # Hard caps: never exceed max_capital_per_trade_pct regardless of Kelly
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
        "kelly_fraction": kelly_fraction,
        "sizing_method": "kelly" if kelly_fraction > 0 else "fixed_fraction",
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

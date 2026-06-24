"""Trade Construction Agent — builds entry/stop/target plans for the top N opportunities."""

from __future__ import annotations

import json as _json
import structlog
import math
import os as _os
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "TradeConstructionAgent"

_SP_PATH = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "../../../../config/strategy_params.json")
)


def _load_strategy_params() -> tuple[float, float | None]:
    """Return (stop_multiplier, target_rr_min) from strategy_params.json."""
    try:
        with open(_SP_PATH) as f:
            sp = _json.load(f)
        return float(sp.get("stop_multiplier", 1.0)), float(sp.get("target_rr_min", 0.0)) or None
    except Exception:
        return 1.0, None


def _build_plan(
    candidate: dict,
    policy: Any,
    stop_mult: float,
    target_rr: float,
    kelly_fraction: float,
) -> dict | None:
    """Construct one trade plan dict. Returns None if price is invalid."""
    symbol = candidate["symbol"]
    current_price = candidate.get("current_price", 0) or 0
    if not current_price or math.isnan(current_price) or current_price <= 0:
        logger.warning("[%s] Skipping %s — current_price is 0 or NaN", AGENT_NAME, symbol)
        return None

    raw_atr = candidate.get("atr", None)
    atr = raw_atr if (raw_atr and not math.isnan(raw_atr) and raw_atr > 0) else current_price * 0.015

    pattern = candidate.get("pattern", "NONE")
    vwap = candidate.get("vwap", current_price) or current_price

    # Entry price
    if pattern in ("ORB", "BREAKOUT"):
        entry_price = current_price
    elif pattern == "VWAP_CROSS":
        entry_price = max(current_price, vwap)
    else:
        entry_price = current_price

    # Stop loss
    orb_low = candidate.get("orb_low", None)
    if pattern == "ORB" and orb_low and orb_low > 0 and orb_low < entry_price:
        stop_price = round(orb_low, 2)
        stop_distance = entry_price - stop_price
    else:
        stop_distance = atr * stop_mult
        stop_price = round(entry_price - stop_distance, 2)

    # Targets: T1 = 1R (book partial profit), T2 = RR-multiple R (let it run)
    rr_mult = target_rr if target_rr else policy.min_risk_reward
    target1 = round(entry_price + stop_distance * 1.0, 2)
    target2 = round(entry_price + stop_distance * rr_mult, 2)

    # Position sizing
    risk_per_share = entry_price - stop_price
    if not risk_per_share or math.isnan(risk_per_share) or risk_per_share <= 0:
        risk_per_share = atr if atr > 0 else entry_price * 0.015

    if kelly_fraction > 0:
        kelly_capital = policy.total_capital * kelly_fraction
        qty = int(kelly_capital / entry_price)
    else:
        risk_per_trade = policy.total_capital * policy.max_risk_per_trade_pct / 100
        qty = int(risk_per_trade / risk_per_share)

    max_capital = policy.total_capital * policy.max_capital_per_trade_pct / 100
    qty = min(qty, int(max_capital / entry_price))
    qty = max(qty, 1)

    rr = (target1 - entry_price) / (entry_price - stop_price) if (entry_price - stop_price) > 0 else 0

    return {
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
        "score": candidate.get("score", 0),
        "catalyst_reason": candidate.get("catalyst_reason", ""),
        "kelly_fraction": kelly_fraction,
        "sizing_method": "kelly" if kelly_fraction > 0 else "fixed_fraction",
    }


def trade_construction_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Constructing trade plans", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    scored = state.get("scored_opportunities", [])

    if not scored:
        entry = audit_entry(agent=AGENT_NAME, action="no_opportunity", data={})
        return {"trade_plan": {}, "trade_plans": [], "audit_trail": [entry]}

    stop_mult, target_rr = _load_strategy_params()
    kelly_fraction = state.get("kelly_fraction", 0.0)

    # How many slots are open?
    current_positions = [p for p in state.get("positions", []) if p.get("status") == "OPEN"]
    slots = max(0, policy.max_concurrent_positions - len(current_positions))
    n_plans = min(len(scored), slots)

    trade_plans: list[dict] = []
    for candidate in scored[:n_plans]:
        plan = _build_plan(candidate, policy, stop_mult, target_rr or 0.0, kelly_fraction)
        if plan:
            trade_plans.append(plan)

    if not trade_plans:
        entry = audit_entry(agent=AGENT_NAME, action="no_valid_price", data={"scored": len(scored)})
        return {"trade_plan": {}, "trade_plans": [], "audit_trail": [entry]}

    first_plan = trade_plans[0]
    msgs = [
        create_message(source=AGENT_NAME, target="ExecutionAgent", symbol=p["symbol"], payload=p)
        for p in trade_plans
    ]
    entries = [
        audit_entry(agent=AGENT_NAME, action="trade_constructed", data=p)
        for p in trade_plans
    ]

    for p in trade_plans:
        logger.info(
            "[%s] Plan: %s Entry=%.2f Stop=%.2f T1=%.2f T2=%.2f Qty=%d R/R=%.2f",
            AGENT_NAME, p["symbol"], p["entry"], p["stop"], p["target1"], p["target2"], p["qty"], p["rr"],
        )

    return {
        "trade_plan": first_plan,   # backward compat
        "trade_plans": trade_plans,
        "messages": msgs,
        "audit_trail": entries,
    }

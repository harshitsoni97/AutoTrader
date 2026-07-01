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


def _adaptive_target_rr(candidate: dict, floor_rr: float = 1.5) -> float:
    """Pick the runner's reward:risk multiple (T2) dynamically per setup.

    Rationale for the method (algorithmic, not LLM):
      - The multiple must be deterministic, backtestable, and tunable by the RL
        agent. An LLM call would be non-reproducible, slow, and unauditable for
        a single number; LLMs add value on qualitative review, not numeric sizing.
      - No external "optimal RR" API exists that beats using the signals we
        already compute. The standard practitioner approach (ATR/Van-Tharp style)
        is to let trend strength decide how far to let a winner run.

    Inputs (all already on the candidate):
      - ADX  → trend strength. Strong trend = let it run further (higher RR).
      - volume multiple → conviction. Strong participation bumps the target.
      - RSI  → very overbought reduces the runner (mean-reversion risk).
    Output is clamped to [floor_rr, 3.5] and always > 1.0 so T2 > T1 (=1R).
    """
    adx = candidate.get("adx", 20) or 20
    if adx >= 40:
        rr = 3.0
    elif adx >= 30:
        rr = 2.5
    elif adx >= 25:
        rr = 2.0
    elif adx >= 20:
        rr = 1.7
    else:
        rr = 1.5

    # Volume conviction: strong participation lets the runner stretch.
    vol_mult = candidate.get("volume_multiple") or candidate.get("rvol") or 0
    if vol_mult and vol_mult >= 2.5:
        rr += 0.5
    elif vol_mult and vol_mult >= 1.8:
        rr += 0.25

    # Overbought guard: very stretched RSI = pull the runner in (reversion risk).
    rsi = candidate.get("rsi", 50) or 50
    if rsi >= 80:
        rr -= 0.5
    elif rsi >= 75:
        rr -= 0.25

    return round(max(floor_rr, min(rr, 3.5)), 2)


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

    # Use DAILY ATR for swing-level intraday stops/targets. The 30-min ATR is the
    # range of a single bar (~0.5% of price) and produces absurdly tight targets
    # (e.g. DRREDDY T1 at +0.68% while the stock moved +2.3%). Daily ATR reflects
    # the move we actually hold for. Fall back to intraday/atr, then a 1.5% proxy.
    raw_atr = candidate.get("daily_atr") or candidate.get("atr") or 0
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

    # Targets: T1 = 1R (book partial profit), T2 = adaptive R (let the runner run).
    # The runner multiple is chosen per-setup from trend strength / conviction
    # (see _adaptive_target_rr). The RL-tuned target_rr is used only as a FLOOR so
    # the tuner can never collapse T2 onto T1 (the old target_rr_min=1.0 bug).
    floor_rr = max(1.5, target_rr or 0.0)
    rr_mult = _adaptive_target_rr(candidate, floor_rr=floor_rr)
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
        "target2_rr": rr_mult,
        "atr_used": round(atr, 2),
        "qty": qty,
        "position_size_inr": round(qty * entry_price, 2),
        "risk_inr": round(qty * risk_per_share, 2),
        "reward_inr": round(qty * (target1 - entry_price), 2),
        "rr": round(rr, 2),
        "pattern": pattern,
        "sector": candidate.get("sector"),
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

    # Portfolio heat: cap concentration so the book isn't 3 correlated names in
    # one sector (e.g. 2026-06-25's all-pharma picks). Keep plans greedily by
    # score while no sector exceeds max_sector_exposure_pct of capital. Each plan
    # is charged its worst-case allocation (max_capital_per_trade_pct); existing
    # open positions pre-charge their sector.
    heat_dropped: list[str] = []
    if getattr(policy, "max_sector_exposure_pct", 0):
        sector_cap_pct = policy.max_sector_exposure_pct
        per_trade_pct = policy.max_capital_per_trade_pct
        cap = policy.total_capital
        sector_used_pct: dict[str, float] = {}
        for op in current_positions:
            sec = op.get("sector") or "UNKNOWN"
            sector_used_pct[sec] = sector_used_pct.get(sec, 0.0) + (op.get("position_size_inr", 0) / cap * 100 if cap else 0)
        kept: list[dict] = []
        for p in trade_plans:
            sec = p.get("sector") or "UNKNOWN"
            if sector_used_pct.get(sec, 0.0) + per_trade_pct > sector_cap_pct + 1e-6:
                heat_dropped.append(p["symbol"])
                logger.info("[%s] Portfolio heat: dropping %s — sector '%s' would exceed %.0f%%",
                            AGENT_NAME, p["symbol"], sec, sector_cap_pct)
                continue
            sector_used_pct[sec] = sector_used_pct.get(sec, 0.0) + per_trade_pct
            kept.append(p)
        trade_plans = kept

    if not trade_plans:
        entry = audit_entry(agent=AGENT_NAME, action="all_dropped_portfolio_heat",
                            data={"dropped": heat_dropped})
        return {"trade_plan": {}, "trade_plans": [], "audit_trail": [entry]}

    # Portfolio-level allocation: distribute total_capital across plans weighted by score.
    # Each plan's share = (score / sum_scores) × total_capital, capped at max_capital_per_trade_pct.
    # The whole book is then scaled by the confidence-size multiplier so
    # moderate-confidence days deploy proportionally less capital.
    from autotrader.core.sizing import confidence_size_mult
    confidence = state.get("market_confidence", 0.0)
    size_mult = state.get("confidence_size_mult")
    if size_mult is None:
        size_mult = confidence_size_mult(confidence, policy)
    size_mult = size_mult if size_mult and size_mult > 0 else 1.0

    total_capital = policy.total_capital
    max_per_trade = total_capital * policy.max_capital_per_trade_pct / 100
    total_score = sum(p["score"] for p in trade_plans) or 1.0
    for p in trade_plans:
        weight = p["score"] / total_score
        allocated = min(weight * total_capital, max_per_trade) * size_mult
        p["confidence_size_mult"] = size_mult
        qty = max(1, int(allocated / p["entry"]))
        p["qty"] = qty
        p["position_size_inr"] = round(qty * p["entry"], 2)
        p["risk_inr"] = round(qty * (p["entry"] - p["stop"]), 2)
        p["reward_inr"] = round(qty * (p["target1"] - p["entry"]), 2)
        p["allocation_pct"] = round(weight * 100, 1)
        logger.info(
            "[%s] Allocation: %s score=%.1f weight=%.1f%% allocated=₹%.0f qty=%d",
            AGENT_NAME, p["symbol"], p["score"], weight * 100, allocated, qty,
        )

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

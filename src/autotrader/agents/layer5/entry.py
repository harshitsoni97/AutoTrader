"""Intraday Entry Agent — books pre-market PLANS at real prices after the open.

Pre-market produces plans only (entry/stop/targets) — it does NOT fill anything,
because the market isn't open yet and a pre-open "fill" is fiction. This agent
runs during market hours and books those plans against the ACTUAL live price:

  - live price unavailable            → skip (can't book)
  - already >= target1 (move is gone) → skip (overextended; would be chasing)
  - already <= stop                   → skip (setup already broken)
  - otherwise                         → BOOK at the live price (+ slippage)

Fill = the real market price, not the stale pre-open plan level. This is the
honest "book after the open" step, for dry-run and live alike. Runs once per day
(when there are plans and no positions yet).
"""

from __future__ import annotations

import hashlib
import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.notifications import get_notifier
from autotrader.tools.price_utils import live_ltp

logger = structlog.get_logger()

AGENT_NAME = "EntryAgent"


def _idempotency_key(symbol: str, run_date: str, price: float, qty: int) -> str:
    raw = f"{symbol}|{run_date}|{price:.2f}|{qty}"
    return "EN-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def entry_agent(state: TradingState) -> dict[str, Any]:
    plans: list[dict] = state.get("trade_plans", []) or []
    existing = state.get("positions", [])
    # Only book plans we haven't already entered (idempotent across intraday cycles).
    entered_symbols = {p.get("symbol") for p in existing}
    to_book = [p for p in plans if p.get("symbol") and p["symbol"] not in entered_symbols]

    if not to_book:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="nothing_to_book",
                                            data={"plans": len(plans), "open": len(existing)})]}

    cfg = load_config()
    policy = cfg.trading_policy

    # Enforce the confidence floor at booking time too (defense in depth — a plan
    # from a saved session must not book below the floor).
    confidence = state.get("market_confidence", 0.0)
    floor = getattr(policy, "confidence_min_trade", 0.65)
    if confidence and confidence < floor:
        return {"audit_trail": [audit_entry(agent=AGENT_NAME, action="skip_below_confidence_floor",
                                            data={"confidence": confidence, "floor": floor})]}

    is_dry_run = state.get("dry_run", True)
    run_date = state.get("run_date", "")
    half_spread_bps = getattr(policy, "dry_run_slippage_bps", 4.0)
    impact_bps_per_lakh = getattr(policy, "dry_run_impact_bps_per_lakh", 1.5)
    notifier = get_notifier(cfg.notifications)

    from autotrader.core.slippage import slipped_fill

    new_positions: list[dict] = []
    new_orders: list[dict] = []
    msgs: list[dict] = []
    audit: list[dict] = []
    booked = 0

    for plan in to_book:
        symbol = plan["symbol"]
        qty = plan.get("qty", 0)
        stop = plan.get("stop", 0)
        target1 = plan.get("target1", 0)
        target2 = plan.get("target2", 0)

        live = live_ltp(symbol)
        if live is None or live <= 0:
            audit.append(audit_entry(agent=AGENT_NAME, action="skip_no_live_price", data={"symbol": symbol}))
            logger.info("[%s] Skip %s — no live price", AGENT_NAME, symbol)
            continue
        if target1 and live >= target1:
            audit.append(audit_entry(agent=AGENT_NAME, action="skip_overextended",
                                     data={"symbol": symbol, "live": live, "target1": target1}))
            logger.info("[%s] Skip %s — already past T1 (live %.2f >= %.2f)", AGENT_NAME, symbol, live, target1)
            continue
        if stop and live <= stop:
            audit.append(audit_entry(agent=AGENT_NAME, action="skip_below_stop",
                                     data={"symbol": symbol, "live": live, "stop": stop}))
            logger.info("[%s] Skip %s — already at/below stop (live %.2f <= %.2f)", AGENT_NAME, symbol, live, stop)
            continue
        # Extension guard: don't chase a name that has already run too far above
        # the planned entry at the open (real-desk "don't chase" rule).
        plan_entry = plan.get("entry", 0) or 0
        atr = plan.get("atr_used", 0) or 0
        max_ext = getattr(policy, "max_entry_extension_atr", 1.5)
        if plan_entry and atr > 0 and (live - plan_entry) > max_ext * atr:
            audit.append(audit_entry(agent=AGENT_NAME, action="skip_extended_at_open",
                                     data={"symbol": symbol, "live": live, "plan_entry": plan_entry,
                                           "ext_atr": round((live - plan_entry) / atr, 2)}))
            logger.info("[%s] Skip %s — extended %.1f ATR above plan entry at open (live %.2f, plan %.2f)",
                        AGENT_NAME, symbol, (live - plan_entry) / atr, live, plan_entry)
            continue

        # Book at the real live price (adverse slippage on the buy).
        fill_price, slip = slipped_fill(live, qty, "BUY", half_spread_bps, impact_bps_per_lakh)

        # Re-anchor stop/targets to the ACTUAL fill, preserving the plan's ATR-based
        # distances. The plan levels were anchored to the pre-open plan entry; booking
        # at a different live price would otherwise distort R:R (e.g. a plan T1 only
        # +1.2% above a higher fill). This keeps the intended risk/reward intact.
        plan_entry_lvl = plan.get("entry") or 0
        if plan_entry_lvl > 0:
            stop_dist = plan_entry_lvl - stop if stop else 0
            t1_dist = target1 - plan_entry_lvl if target1 else 0
            t2_dist = target2 - plan_entry_lvl if target2 else 0
            if stop_dist > 0:
                stop = round(fill_price - stop_dist, 2)
            if t1_dist > 0:
                target1 = round(fill_price + t1_dist, 2)
            if t2_dist > 0:
                target2 = round(fill_price + t2_dist, 2)

        tag = _idempotency_key(symbol, run_date, fill_price, qty)
        order = {
            "order_id": f"{'DRY' if is_dry_run else 'LIVE'}-{tag}",
            "symbol": symbol, "qty": qty, "side": "BUY",
            "order_type": "DRY_RUN" if is_dry_run else "MARKET",
            "requested_price": live, "fill_price": fill_price, "slippage": slip,
            "status": "DRY_RUN_ASSUMED" if is_dry_run else "FILLED", "tag": tag,
        }
        position = {
            "symbol": symbol, "qty": qty,
            "entry_price": fill_price,      # REAL fill, not the pre-open plan level
            "assumed_entry": plan.get("entry"),
            "plan_entry": plan.get("entry"),
            "stop": stop, "target1": target1, "target2": target2,
            "target2_rr": plan.get("target2_rr"), "atr_used": plan.get("atr_used"),
            "sector": plan.get("sector"), "pattern": plan.get("pattern"),
            "score": plan.get("score"), "order_id": order["order_id"],
            "status": "OPEN", "unrealized_pnl": 0.0, "dry_run": is_dry_run,
            "booked_intraday": True,
        }
        notifier.notify_order(order)
        new_orders.append(order)
        new_positions.append(position)
        booked += 1
        logger.info("[%s] Booked %s x%d @ %.2f (plan %.2f, live %.2f)",
                    AGENT_NAME, symbol, qty, fill_price, plan.get("entry", 0), live)
        msgs.append(create_message(source=AGENT_NAME, target="MonitoringAgent", symbol=symbol,
                                   payload={"order_id": order["order_id"], "fill_price": fill_price, "qty": qty}))
        audit.append(audit_entry(agent=AGENT_NAME, action="entry_booked", data={
            "symbol": symbol, "qty": qty, "plan_entry": plan.get("entry"),
            "live": live, "fill_price": fill_price,
        }))

    return {
        "orders": new_orders,
        "positions": existing + new_positions,
        "daily_trades_taken": state.get("daily_trades_taken", 0) + booked,
        "messages": msgs,
        "audit_trail": audit,
    }

"""Intra-day Re-entry Agent.

Runs every monitoring cycle. When a position's target1 is hit (partial exit
already booked by MonitoringAgent), this agent redeploys the freed capital
into the next-best pre-market opportunity that is not already in a position.

Capital freed = (half_qty sold at target1) × target1 price.
New position is sized to that freed capital, capped at max_capital_per_trade_pct.
"""

from __future__ import annotations

import hashlib
import structlog
import math
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState

logger = structlog.get_logger()

AGENT_NAME = "IntraReentryAgent"


def _instrument_key(symbol: str) -> str | None:
    """Look up Upstox instrument key for a symbol using the technical_structure map."""
    try:
        from autotrader.agents.layer2.technical_structure import _load_instrument_map
        return _load_instrument_map().get(symbol)
    except Exception:
        return None


def _live_price(symbol: str) -> float | None:
    """Fetch live LTP from Upstox. Returns None if unavailable."""
    ikey = _instrument_key(symbol)
    if not ikey:
        return None
    try:
        from autotrader.tools import upstox_data
        result = upstox_data.get_ltp([ikey])
        if result:
            return result.get(ikey)
    except Exception as exc:
        logger.warning("[%s] LTP fetch failed for %s: %s", AGENT_NAME, symbol, exc)
    return None


def _idempotency_key(symbol: str, run_date: str, entry: float, qty: int) -> str:
    raw = f"RE-{symbol}|{run_date}|{entry:.2f}|{qty}"
    return "RE-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def _dry_run_fill(symbol: str, qty: int, entry: float, tag: str) -> dict:
    return {
        "order_id": f"DRY-{tag}",
        "symbol": symbol,
        "qty": qty,
        "side": "BUY",
        "order_type": "DRY_RUN",
        "requested_price": entry,
        "fill_price": entry,
        "slippage": 0.0,
        "status": "DRY_RUN_ASSUMED",
        "tag": tag,
        "reentry": True,
    }


def intra_reentry_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Checking for re-entry opportunities", AGENT_NAME)

    cfg = load_config()
    policy = cfg.trading_policy
    positions: list[dict] = state.get("positions", [])
    scored: list[dict] = state.get("scored_opportunities", [])
    run_date = state.get("run_date", "")
    is_dry_run = state.get("dry_run", True)
    existing_tags = {o.get("tag") for o in state.get("orders", [])}

    # Symbols that already have or had a position (no re-entry on same stock by default)
    occupied_symbols: set[str] = {p["symbol"] for p in positions}

    # Find positions where target1 was hit but we haven't yet triggered a re-entry
    trigger_positions = [
        p for p in positions
        if p.get("target1_hit") and not p.get("reentry_triggered") and p.get("status") == "OPEN"
    ]

    if not trigger_positions:
        entry = audit_entry(agent=AGENT_NAME, action="no_reentry_trigger", data={})
        return {"audit_trail": [entry]}

    # Daily trade budget remaining
    daily_trades = state.get("daily_trades_taken", 0)
    daily_budget_left = policy.max_daily_trades - daily_trades
    if daily_budget_left <= 0:
        entry = audit_entry(agent=AGENT_NAME, action="daily_limit_reached",
                            data={"daily_trades": daily_trades})
        logger.info("[%s] Daily trade limit reached — skipping re-entry", AGENT_NAME)
        return {"audit_trail": [entry]}

    # Filter the pre-market ranked list to symbols not yet in any position
    candidates = [s for s in scored if s["symbol"] not in occupied_symbols]

    if not candidates:
        entry = audit_entry(agent=AGENT_NAME, action="no_reentry_candidates", data={})
        logger.info("[%s] No unused ranked opportunities available for re-entry", AGENT_NAME)
        return {"audit_trail": [entry]}

    new_orders: list[dict] = []
    new_positions: list[dict] = []
    updated_positions: list[dict] = []
    audit_entries: list[dict] = []
    msgs: list[dict] = []
    trades_placed = 0

    for trig_pos in trigger_positions:
        if trades_placed >= daily_budget_left or not candidates:
            break

        # Freed capital = originally half the position at target1 price
        orig_qty = trig_pos.get("qty", 0) * 2  # qty was halved when target1 hit
        freed_capital = (orig_qty // 2) * trig_pos.get("target1", trig_pos["entry_price"])

        # Pick next best candidate
        next_cand = candidates.pop(0)
        symbol = next_cand["symbol"]

        # Get live price; fall back to pre-market price
        live_price = _live_price(symbol)
        entry_price = live_price if (live_price and live_price > 0) else next_cand.get("current_price", 0)
        if not entry_price or math.isnan(entry_price) or entry_price <= 0:
            logger.warning("[%s] Cannot get price for %s — skipping re-entry", AGENT_NAME, symbol)
            audit_entries.append(audit_entry(
                agent=AGENT_NAME, action="reentry_skipped_no_price",
                data={"symbol": symbol},
            ))
            continue

        # Size to freed capital, hard-capped at max_capital_per_trade_pct
        max_capital = policy.total_capital * policy.max_capital_per_trade_pct / 100
        capital_to_deploy = min(freed_capital, max_capital)
        qty = max(1, int(capital_to_deploy / entry_price))

        raw_atr = next_cand.get("atr", None)
        atr = raw_atr if (raw_atr and not math.isnan(raw_atr) and raw_atr > 0) else entry_price * 0.015
        stop_price = round(entry_price - atr, 2)
        target1 = round(entry_price + atr * 1.0, 2)
        target2 = round(entry_price + atr * policy.min_risk_reward, 2)

        tag = _idempotency_key(symbol, run_date, entry_price, qty)
        if tag in existing_tags:
            logger.info("[%s] Re-entry duplicate suppressed: %s", AGENT_NAME, symbol)
            continue

        if is_dry_run:
            order = _dry_run_fill(symbol, qty, entry_price, tag)
        else:
            from autotrader.tools.broker_tools import ORDER_TYPE_LIMIT, get_broker
            broker = get_broker(cfg.broker)
            order = broker.place_order(
                symbol=symbol, qty=qty, side="BUY",
                order_type=ORDER_TYPE_LIMIT, price=entry_price, tag=tag,
            )

        from autotrader.tools.notifications import get_notifier
        get_notifier(cfg.notifications).notify_order(order)

        existing_tags.add(tag)
        occupied_symbols.add(symbol)
        new_orders.append(order)
        new_positions.append({
            "symbol": symbol,
            "qty": qty,
            "entry_price": order["fill_price"],
            "assumed_entry": entry_price,
            "stop": stop_price,
            "target1": target1,
            "target2": target2,
            "order_id": order["order_id"],
            "status": "OPEN",
            "unrealized_pnl": 0.0,
            "dry_run": is_dry_run,
            "reentry": True,
            "triggered_by": trig_pos["symbol"],
            "freed_capital": round(freed_capital, 2),
        })
        # Mark the trigger position so we don't re-enter again for it
        updated_positions.append({**trig_pos, "reentry_triggered": True})
        trades_placed += 1

        logger.info(
            "[%s] Re-entry: %s x%d @ %.2f | Stop=%.2f T1=%.2f (freed ₹%.0f from %s target1)",
            AGENT_NAME, symbol, qty, entry_price, stop_price, target1,
            freed_capital, trig_pos["symbol"],
        )
        msgs.append(create_message(
            source=AGENT_NAME, target="MonitoringAgent",
            symbol=symbol,
            payload={"order_id": order["order_id"], "fill_price": order["fill_price"],
                     "qty": qty, "reentry": True, "triggered_by": trig_pos["symbol"]},
        ))
        audit_entries.append(audit_entry(agent=AGENT_NAME, action="reentry_executed", data={
            "symbol": symbol, "qty": qty, "entry": entry_price,
            "stop": stop_price, "target1": target1, "target2": target2,
            "freed_capital": round(freed_capital, 2),
            "triggered_by": trig_pos["symbol"],
            "dry_run": is_dry_run,
        }))

    if not new_orders:
        return {"audit_trail": audit_entries or [audit_entry(agent=AGENT_NAME, action="no_reentry_placed", data={})]}

    # Merge updated trigger positions back into the positions list
    trigger_ids = {p["order_id"] for p in trigger_positions}
    final_positions = [
        next((up for up in updated_positions if up["order_id"] == p["order_id"]), p)
        for p in positions
    ] + new_positions

    return {
        "orders": new_orders,
        "positions": final_positions,
        "daily_trades_taken": state.get("daily_trades_taken", 0) + trades_placed,
        "messages": msgs,
        "audit_trail": audit_entries,
    }

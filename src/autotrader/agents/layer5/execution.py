"""Execution Agent — places orders for all trade plans.

In dry-run mode no real broker call is made. Assumed fill = plan entry price,
zero slippage. Post-market learning compares assumed vs actual end-of-day price.
"""

from __future__ import annotations

import hashlib
import structlog
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import ORDER_TYPE_LIMIT, get_broker
from autotrader.tools.notifications import get_notifier

logger = structlog.get_logger()

AGENT_NAME = "ExecutionAgent"


def _idempotency_key(symbol: str, run_date: str, entry: float, qty: int) -> str:
    raw = f"{symbol}|{run_date}|{entry:.2f}|{qty}"
    return "AT-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def _dry_run_fill(trade_plan: dict, tag: str, half_spread_bps: float, impact_bps_per_lakh: float) -> dict:
    from autotrader.core.slippage import slipped_fill
    entry = trade_plan["entry"]
    qty = trade_plan["qty"]
    fill_price, slip = slipped_fill(entry, qty, "BUY", half_spread_bps, impact_bps_per_lakh)
    return {
        "order_id": f"DRY-{tag}",
        "symbol": trade_plan["symbol"],
        "qty": qty,
        "side": "BUY",
        "order_type": "DRY_RUN",
        "requested_price": entry,
        "fill_price": fill_price,      # adverse fill, not the plan price
        "slippage": slip,
        "status": "DRY_RUN_ASSUMED",
        "tag": tag,
    }


def _execute_single(
    trade_plan: dict,
    run_date: str,
    is_dry_run: bool,
    broker: Any,
    existing_tags: set[str],
    half_spread_bps: float = 0.0,
    impact_bps_per_lakh: float = 0.0,
) -> tuple[dict | None, dict | None, str | None]:
    """Execute one plan. Returns (order, position, skip_reason)."""
    symbol = trade_plan["symbol"]
    qty = trade_plan["qty"]
    entry_price = trade_plan["entry"]
    tag = _idempotency_key(symbol, run_date, entry_price, qty)

    if tag in existing_tags:
        logger.warning("[%s] Duplicate suppressed: tag=%s symbol=%s", AGENT_NAME, tag, symbol)
        return None, None, f"duplicate:{tag}"

    if is_dry_run:
        order = _dry_run_fill(trade_plan, tag, half_spread_bps, impact_bps_per_lakh)
        logger.info("[%s] DRY RUN — fill %s x%d @ %.2f (plan %.2f, slip %.2f)",
                    AGENT_NAME, symbol, qty, order["fill_price"], entry_price, order["slippage"])
    else:
        order = broker.place_order(
            symbol=symbol, qty=qty, side="BUY",
            order_type=ORDER_TYPE_LIMIT, price=entry_price, tag=tag,
        )
        slippage_bps = (order["slippage"] / entry_price) * 10000
        logger.info(
            "[%s] LIVE order %s filled: %s x%d @ %.2f (slippage: %.1f bps)",
            AGENT_NAME, order["order_id"], symbol, qty, order["fill_price"], slippage_bps,
        )

    fill_price = order["fill_price"]
    position = {
        "symbol": symbol,
        "qty": qty,
        "entry_price": fill_price,
        "assumed_entry": entry_price,
        "stop": trade_plan["stop"],
        "target1": trade_plan["target1"],
        "target2": trade_plan["target2"],
        # Plan metadata carried for the trade journal / future RL tuning
        "target2_rr": trade_plan.get("target2_rr"),
        "atr_used": trade_plan.get("atr_used"),
        "pattern": trade_plan.get("pattern"),
        "score": trade_plan.get("score"),
        "order_id": order["order_id"],
        "status": "OPEN",
        "unrealized_pnl": 0.0,
        "dry_run": is_dry_run,
    }
    return order, position, None


def execution_agent(state: TradingState) -> dict[str, Any]:
    # Prefer the full trade_plans list; fall back to single trade_plan for compat
    trade_plans: list[dict] = state.get("trade_plans", [])
    if not trade_plans:
        single = state.get("trade_plan", {})
        if single:
            trade_plans = [single]

    if not trade_plans:
        entry = audit_entry(agent=AGENT_NAME, action="no_trade_plan", data={})
        return {"audit_trail": [entry]}

    is_dry_run = state.get("dry_run", True)
    run_date = state.get("run_date", "")
    existing_tags = {o.get("tag") for o in state.get("orders", [])}

    cfg = load_config()
    broker = get_broker(cfg.broker) if not is_dry_run else None
    notifier = get_notifier(cfg.notifications)
    half_spread_bps = getattr(cfg.trading_policy, "dry_run_slippage_bps", 4.0)
    impact_bps_per_lakh = getattr(cfg.trading_policy, "dry_run_impact_bps_per_lakh", 1.5)

    all_orders: list[dict] = []
    all_positions: list[dict] = []
    audit_entries: list[dict] = []
    msgs: list[dict] = []
    trades_placed = 0

    for plan in trade_plans:
        order, position, skip_reason = _execute_single(
            plan, run_date, is_dry_run, broker, existing_tags,
            half_spread_bps, impact_bps_per_lakh,
        )
        if skip_reason:
            audit_entries.append(audit_entry(
                agent=AGENT_NAME, action="duplicate_suppressed",
                data={"reason": skip_reason, "symbol": plan["symbol"]},
            ))
            continue

        notifier.notify_order(order)
        existing_tags.add(order["tag"])
        all_orders.append(order)
        all_positions.append(position)
        trades_placed += 1

        slippage_bps = 0.0 if is_dry_run else (order["slippage"] / plan["entry"]) * 10000
        msgs.append(create_message(
            source=AGENT_NAME, target="MonitoringAgent",
            symbol=plan["symbol"],
            payload={
                "order_id": order["order_id"],
                "fill_price": order["fill_price"],
                "qty": plan["qty"],
                "slippage_bps": round(slippage_bps, 2),
                "dry_run": is_dry_run,
            },
        ))
        audit_entries.append(audit_entry(agent=AGENT_NAME, action="order_placed", data={
            "order_id": order["order_id"],
            "symbol": plan["symbol"],
            "qty": plan["qty"],
            "requested_price": plan["entry"],
            "fill_price": order["fill_price"],
            "slippage_bps": round(slippage_bps, 2),
            "dry_run": is_dry_run,
            "mode": "DRY_RUN" if is_dry_run else "LIVE",
        }))

    return {
        "orders": all_orders,
        "positions": state.get("positions", []) + all_positions,
        "daily_trades_taken": state.get("daily_trades_taken", 0) + trades_placed,
        "messages": msgs,
        "audit_trail": audit_entries,
    }

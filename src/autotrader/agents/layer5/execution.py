"""Execution Agent — places orders via the broker interface.

In dry-run mode (trading_policy.dry_run = true) no real broker call is made.
The assumed fill is the plan entry price with zero slippage. Post-market
learning compares this assumed fill against the actual end-of-day price.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import ORDER_TYPE_LIMIT, get_broker

logger = logging.getLogger(__name__)

AGENT_NAME = "ExecutionAgent"


def _idempotency_key(symbol: str, run_date: str, entry: float, qty: int) -> str:
    """Stable tag identifying this exact trade intent for the session.

    A repeated execution run for the same plan produces the same key, so the
    order is never placed twice (deduped against existing orders and via the
    broker tag).
    """
    raw = f"{symbol}|{run_date}|{entry:.2f}|{qty}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:10]
    return f"AT-{digest}"


def _dry_run_fill(trade_plan: dict, tag: str) -> dict:
    """Simulate a fill at plan entry price with zero slippage."""
    return {
        "order_id": f"DRY-{tag}",
        "symbol": trade_plan["symbol"],
        "qty": trade_plan["qty"],
        "side": "BUY",
        "order_type": "DRY_RUN",
        "requested_price": trade_plan["entry"],
        "fill_price": trade_plan["entry"],
        "slippage": 0.0,
        "status": "DRY_RUN_ASSUMED",
        "tag": tag,
    }


def execution_agent(state: TradingState) -> dict[str, Any]:
    trade_plan = state.get("trade_plan", {})
    if not trade_plan:
        entry = audit_entry(agent=AGENT_NAME, action="no_trade_plan", data={})
        return {"audit_trail": [entry]}

    symbol = trade_plan["symbol"]
    qty = trade_plan["qty"]
    entry_price = trade_plan["entry"]
    is_dry_run = state.get("dry_run", True)
    run_date = state.get("run_date", "")

    tag = _idempotency_key(symbol, run_date, entry_price, qty)

    # Idempotency guard: if an order with this tag already exists in state, skip.
    for existing in state.get("orders", []):
        if existing.get("tag") == tag:
            logger.warning("[%s] Duplicate execution suppressed for tag=%s", AGENT_NAME, tag)
            dup_entry = audit_entry(agent=AGENT_NAME, action="duplicate_suppressed", data={"tag": tag, "symbol": symbol})
            return {"audit_trail": [dup_entry]}

    cfg = load_config()

    if is_dry_run:
        order = _dry_run_fill(trade_plan, tag)
        logger.info("[%s] DRY RUN — assumed fill: %s x%d @ %.2f", AGENT_NAME, symbol, qty, entry_price)
    else:
        broker = get_broker(cfg.broker)
        order = broker.place_order(
            symbol=symbol,
            qty=qty,
            side="BUY",
            order_type=ORDER_TYPE_LIMIT,
            price=entry_price,
            tag=tag,
        )
        slippage_bps = (order["slippage"] / entry_price) * 10000
        logger.info(
            "[%s] LIVE order %s filled: %s x%d @ %.2f (slippage: %.1f bps)",
            AGENT_NAME, order["order_id"], symbol, qty, order["fill_price"], slippage_bps,
        )

    fill_price = order["fill_price"]
    slippage_bps = (order["slippage"] / entry_price) * 10000 if not is_dry_run else 0.0

    position = {
        "symbol": symbol,
        "qty": qty,
        "entry_price": fill_price,
        "assumed_entry": entry_price,   # always the plan price (for dry-run comparison)
        "stop": trade_plan["stop"],
        "target1": trade_plan["target1"],
        "target2": trade_plan["target2"],
        "order_id": order["order_id"],
        "status": "OPEN",
        "unrealized_pnl": 0.0,
        "dry_run": is_dry_run,
    }

    msg = create_message(
        source=AGENT_NAME, target="MonitoringAgent",
        symbol=symbol,
        payload={
            "order_id": order["order_id"],
            "fill_price": fill_price,
            "qty": qty,
            "slippage_bps": round(slippage_bps, 2),
            "dry_run": is_dry_run,
        },
    )
    entry_audit = audit_entry(agent=AGENT_NAME, action="order_placed", data={
        "order_id": order["order_id"],
        "symbol": symbol,
        "qty": qty,
        "requested_price": entry_price,
        "fill_price": fill_price,
        "slippage_bps": round(slippage_bps, 2),
        "dry_run": is_dry_run,
        "mode": "DRY_RUN" if is_dry_run else "LIVE",
    })

    return {
        "orders": [order],
        "positions": [position],
        "daily_trades_taken": state.get("daily_trades_taken", 0) + 1,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }

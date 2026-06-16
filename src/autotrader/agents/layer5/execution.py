"""Execution Agent — places orders via the broker interface."""

from __future__ import annotations

import logging
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import MockBroker, ORDER_TYPE_LIMIT

logger = logging.getLogger(__name__)

AGENT_NAME = "ExecutionAgent"
_broker = MockBroker()


def execution_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Executing trade", AGENT_NAME)

    trade_plan = state.get("trade_plan", {})
    if not trade_plan:
        entry = audit_entry(agent=AGENT_NAME, action="no_trade_plan", data={})
        return {"audit_trail": [entry]}

    symbol = trade_plan["symbol"]
    qty = trade_plan["qty"]
    entry_price = trade_plan["entry"]

    # Place limit order at entry price
    order = _broker.place_order(
        symbol=symbol,
        qty=qty,
        side="BUY",
        order_type=ORDER_TYPE_LIMIT,
        price=entry_price,
    )

    fill_price = order["fill_price"]
    slippage_inr = order["slippage"] * qty
    slippage_bps = (order["slippage"] / entry_price) * 10000

    position = {
        "symbol": symbol,
        "qty": qty,
        "entry_price": fill_price,
        "stop": trade_plan["stop"],
        "target1": trade_plan["target1"],
        "target2": trade_plan["target2"],
        "order_id": order["order_id"],
        "status": "OPEN",
        "unrealized_pnl": 0.0,
    }

    msg = create_message(
        source=AGENT_NAME, target="MonitoringAgent",
        symbol=symbol,
        payload={
            "order_id": order["order_id"],
            "fill_price": fill_price,
            "qty": qty,
            "slippage_bps": round(slippage_bps, 2),
        },
    )
    entry_audit = audit_entry(agent=AGENT_NAME, action="order_placed", data={
        "order_id": order["order_id"],
        "symbol": symbol,
        "qty": qty,
        "requested_price": entry_price,
        "fill_price": fill_price,
        "slippage_inr": round(slippage_inr, 2),
        "slippage_bps": round(slippage_bps, 2),
    })

    logger.info(
        "[%s] Order %s filled: %s x%d @ %.2f (slippage: %.1f bps)",
        AGENT_NAME, order["order_id"], symbol, qty, fill_price, slippage_bps,
    )

    return {
        "orders": [order],
        "positions": [position],
        "daily_trades_taken": state.get("daily_trades_taken", 0) + 1,
        "messages": [msg],
        "audit_trail": [entry_audit],
    }

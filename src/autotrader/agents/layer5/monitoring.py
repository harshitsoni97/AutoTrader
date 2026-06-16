"""Monitoring Agent — tracks open positions every minute during intraday."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import MockBroker

logger = logging.getLogger(__name__)

AGENT_NAME = "MonitoringAgent"
_broker = MockBroker()


def _get_current_price(symbol: str) -> float:
    quote = _broker.get_quote(symbol)
    return quote.get("ltp", 0.0)


def monitoring_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Monitoring open positions", AGENT_NAME)

    positions = state.get("positions", [])
    if not positions:
        entry = audit_entry(agent=AGENT_NAME, action="no_positions", data={})
        return {"audit_trail": [entry]}

    updated_positions: list[dict] = []
    new_orders: list[dict] = []
    daily_pnl_delta = 0.0
    alerts: list[str] = []
    exits: list[dict] = []

    for pos in positions:
        if pos.get("status") != "OPEN":
            updated_positions.append(pos)
            continue

        symbol = pos["symbol"]
        current_price = _get_current_price(symbol)
        entry_price = pos["entry_price"]
        stop = pos["stop"]
        target1 = pos["target1"]
        target2 = pos["target2"]
        qty = pos["qty"]

        unrealized_pnl = (current_price - entry_price) * qty
        pos = {**pos, "current_price": current_price, "unrealized_pnl": round(unrealized_pnl, 2)}

        # Circuit filter detection (price frozen)
        if current_price == entry_price and pos.get("prev_price", 0) == current_price:
            alerts.append(f"{symbol}: Possible circuit filter — price frozen at {current_price}")

        # Stop loss hit
        if current_price <= stop:
            exit_order = _broker.place_order(symbol, qty, "SELL", price=current_price)
            realized_pnl = (exit_order["fill_price"] - entry_price) * qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "status": "STOPPED", "exit_price": exit_order["fill_price"], "realized_pnl": round(realized_pnl, 2)}
            new_orders.append(exit_order)
            exits.append({"symbol": symbol, "reason": "STOP_HIT", "pnl": round(realized_pnl, 2)})
            alerts.append(f"{symbol}: Stop loss triggered at {current_price:.2f}, PnL: {realized_pnl:.0f}")
            logger.warning("[%s] STOP HIT: %s @ %.2f, PnL=%.0f", AGENT_NAME, symbol, current_price, realized_pnl)

        # Target 2 hit — full exit
        elif current_price >= target2:
            exit_order = _broker.place_order(symbol, qty, "SELL", price=current_price)
            realized_pnl = (exit_order["fill_price"] - entry_price) * qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "status": "TARGET2_HIT", "exit_price": exit_order["fill_price"], "realized_pnl": round(realized_pnl, 2)}
            new_orders.append(exit_order)
            exits.append({"symbol": symbol, "reason": "TARGET2", "pnl": round(realized_pnl, 2)})
            logger.info("[%s] TARGET2 HIT: %s @ %.2f, PnL=%.0f", AGENT_NAME, symbol, current_price, realized_pnl)

        # Target 1 hit — partial exit (half position)
        elif current_price >= target1 and not pos.get("target1_hit"):
            half_qty = max(1, qty // 2)
            exit_order = _broker.place_order(symbol, half_qty, "SELL", price=current_price)
            realized_pnl = (exit_order["fill_price"] - entry_price) * half_qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "target1_hit": True, "qty": qty - half_qty}
            new_orders.append(exit_order)
            alerts.append(f"{symbol}: Target1 hit — partial exit {half_qty} shares @ {current_price:.2f}")
            logger.info("[%s] TARGET1 HIT: %s partial exit %d @ %.2f, PnL=%.0f", AGENT_NAME, symbol, half_qty, current_price, realized_pnl)

        pos["prev_price"] = current_price
        updated_positions.append(pos)

    consecutive_losses = state.get("consecutive_losses", 0)
    if exits:
        last_exit = exits[-1]
        if last_exit["pnl"] < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

    msg = create_message(
        source=AGENT_NAME, target="AlertAgent",
        payload={"alerts": alerts, "exits": exits, "positions_open": sum(1 for p in updated_positions if p.get("status") == "OPEN")},
    )
    entry = audit_entry(agent=AGENT_NAME, action="positions_monitored", data={
        "positions": len(positions),
        "exits": len(exits),
        "alerts": len(alerts),
        "daily_pnl_delta": round(daily_pnl_delta, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "positions": updated_positions,
        "orders": new_orders,
        "daily_pnl": state.get("daily_pnl", 0.0) + daily_pnl_delta,
        "consecutive_losses": consecutive_losses,
        "messages": [msg],
        "audit_trail": [entry],
        "errors": [a for a in alerts if "circuit" in a.lower()],
    }

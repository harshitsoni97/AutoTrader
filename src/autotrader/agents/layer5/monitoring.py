"""Monitoring Agent — tracks open positions every minute during intraday."""

from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Any

from autotrader.core.config import load_config
from autotrader.core.messages import audit_entry, create_message
from autotrader.core.state import TradingState
from autotrader.tools.broker_tools import get_broker
from autotrader.tools.notifications import get_notifier

logger = structlog.get_logger()

AGENT_NAME = "MonitoringAgent"


def _get_current_price(broker, symbol: str, dry_run: bool = False) -> float:
    """Live price for a position.

    In dry-run the MockBroker returns a static fabricated price that never
    moves, so targets/stops could never trigger. Use the real Upstox LTP
    instead so paper trading reacts to the actual market. Live mode uses the
    real broker quote (the execution venue's own feed).
    """
    if dry_run:
        from autotrader.tools.price_utils import live_ltp
        price = live_ltp(symbol)
        if price is not None and price > 0:
            return price
        logger.warning("[%s] dry-run LTP unavailable for %s — falling back to broker quote",
                       AGENT_NAME, symbol)
    quote = broker.get_quote(symbol)
    return quote.get("ltp", 0.0)


def _update_vwap(pos: dict, current_price: float) -> tuple[float, dict]:
    """Incrementally update VWAP using cumulative price×volume.

    Each monitoring tick adds one synthetic trade at current_price with unit volume.
    In production, feed actual minute candle volume from the broker websocket.
    Returns (vwap, updated_pos_fields).
    """
    qty = pos.get("qty", 1)
    cum_pv = pos.get("vwap_cum_pv", pos["entry_price"] * qty)
    cum_vol = pos.get("vwap_cum_vol", float(qty))
    # Add this tick: use position qty as proxy volume weight
    cum_pv += current_price * qty
    cum_vol += qty
    vwap = round(cum_pv / cum_vol, 2) if cum_vol else current_price
    return vwap, {"vwap_cum_pv": cum_pv, "vwap_cum_vol": cum_vol, "vwap": vwap}


def _exit_tag(symbol: str, leg: str, order_id: str) -> str:
    """Idempotent tag for an exit leg so the same exit is never sent twice."""
    return f"EX-{leg}-{order_id}"[:20]


def monitoring_agent(state: TradingState) -> dict[str, Any]:
    logger.info("[%s] Monitoring open positions", AGENT_NAME)

    positions = state.get("positions", [])
    if not positions:
        entry = audit_entry(agent=AGENT_NAME, action="no_positions", data={})
        return {"audit_trail": [entry]}

    cfg = load_config()
    broker = get_broker(cfg.broker)
    notifier = get_notifier(cfg.notifications)
    dry_run = state.get("dry_run", True)

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
        current_price = _get_current_price(broker, symbol, dry_run)
        entry_price = pos["entry_price"]
        stop = pos["stop"]
        target1 = pos["target1"]
        target2 = pos["target2"]
        qty = pos["qty"]
        pos_order_id = pos.get("order_id", symbol)

        unrealized_pnl = (current_price - entry_price) * qty
        vwap, vwap_fields = _update_vwap(pos, current_price)
        pos = {**pos, "current_price": current_price, "unrealized_pnl": round(unrealized_pnl, 2), **vwap_fields}

        # Circuit filter detection (price frozen)
        if current_price == entry_price and pos.get("prev_price", 0) == current_price:
            alerts.append(f"{symbol}: Possible circuit filter — price frozen at {current_price}")

        # VWAP alert: price breaking below VWAP on a long position = exit signal
        if current_price < vwap * 0.998 and not pos.get("vwap_warned"):
            alerts.append(f"{symbol}: Price {current_price:.2f} broke below VWAP {vwap:.2f} — consider exit")
            pos = {**pos, "vwap_warned": True}

        # Stop loss hit — only fire a full exit once
        if current_price <= stop and not pos.get("exit_order_id"):
            exit_order = broker.place_order(
                symbol, qty, "SELL", price=current_price, tag=_exit_tag(symbol, "STOP", pos_order_id)
            )
            realized_pnl = (exit_order["fill_price"] - entry_price) * qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "status": "STOPPED", "exit_price": exit_order["fill_price"],
                   "realized_pnl": round(realized_pnl, 2), "exit_order_id": exit_order["order_id"]}
            new_orders.append(exit_order)
            exits.append({"symbol": symbol, "reason": "STOP_HIT", "pnl": round(realized_pnl, 2)})
            alerts.append(f"{symbol}: Stop loss triggered at {current_price:.2f}, PnL: {realized_pnl:.0f}")
            logger.warning("[%s] STOP HIT: %s @ %.2f, PnL=%.0f", AGENT_NAME, symbol, current_price, realized_pnl)

        # Target 2 hit — full exit, only once
        elif current_price >= target2 and not pos.get("exit_order_id"):
            exit_order = broker.place_order(
                symbol, qty, "SELL", price=current_price, tag=_exit_tag(symbol, "TGT2", pos_order_id)
            )
            realized_pnl = (exit_order["fill_price"] - entry_price) * qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "status": "TARGET2_HIT", "exit_price": exit_order["fill_price"],
                   "realized_pnl": round(realized_pnl, 2), "exit_order_id": exit_order["order_id"]}
            new_orders.append(exit_order)
            exits.append({"symbol": symbol, "reason": "TARGET2", "pnl": round(realized_pnl, 2)})
            logger.info("[%s] TARGET2 HIT: %s @ %.2f, PnL=%.0f", AGENT_NAME, symbol, current_price, realized_pnl)

        # Target 1 hit — partial exit (half position), only once
        elif current_price >= target1 and not pos.get("target1_hit"):
            half_qty = max(1, qty // 2)
            exit_order = broker.place_order(
                symbol, half_qty, "SELL", price=current_price, tag=_exit_tag(symbol, "TGT1", pos_order_id)
            )
            realized_pnl = (exit_order["fill_price"] - entry_price) * half_qty
            daily_pnl_delta += realized_pnl
            pos = {**pos, "target1_hit": True, "qty": qty - half_qty}
            new_orders.append(exit_order)
            alerts.append(f"{symbol}: Target1 hit — partial exit {half_qty} shares @ {current_price:.2f}")
            logger.info("[%s] TARGET1 HIT: %s partial exit %d @ %.2f, PnL=%.0f", AGENT_NAME, symbol, half_qty, current_price, realized_pnl)

        pos["prev_price"] = current_price
        updated_positions.append(pos)

    # Notify on each exit (stop / target) — never raises into the trading path.
    for exit_info in exits:
        notifier.notify_exit(exit_info)

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

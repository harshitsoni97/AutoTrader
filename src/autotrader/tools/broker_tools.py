"""Broker interface and mock implementation."""

from __future__ import annotations

import logging
import random
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_VWAP = "VWAP"

STATUS_OPEN = "OPEN"
STATUS_FILLED = "FILLED"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED = "REJECTED"


class BrokerInterface(ABC):
    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,  # BUY | SELL
        order_type: str = ORDER_TYPE_MARKET,
        price: float | None = None,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def get_orders(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def get_quote(self, symbol: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        ...


class MockBroker(BrokerInterface):
    """Simulated broker for paper trading and testing."""

    def __init__(self, slippage_bps: float = 5.0):
        self._orders: dict[str, dict] = {}
        self._positions: dict[str, dict] = {}
        self._slippage_bps = slippage_bps  # basis points

    def _simulate_fill_price(self, price: float, side: str) -> float:
        slip = self._slippage_bps / 10000
        factor = 1 + slip if side == "BUY" else 1 - slip
        return round(price * factor, 2)

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = ORDER_TYPE_MARKET,
        price: float | None = None,
    ) -> dict[str, Any]:
        order_id = str(uuid.uuid4())[:8].upper()
        # Simulate a fill price
        market_price = price or self._get_mock_price(symbol)
        fill_price = self._simulate_fill_price(market_price, side)
        slippage = abs(fill_price - market_price)

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "order_type": order_type,
            "requested_price": price,
            "fill_price": fill_price,
            "slippage": round(slippage, 4),
            "status": STATUS_FILLED,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._orders[order_id] = order

        # Update mock position
        if side == "BUY":
            self._positions[symbol] = {
                "symbol": symbol,
                "qty": qty,
                "avg_price": fill_price,
                "current_price": fill_price,
                "unrealized_pnl": 0.0,
            }
        elif side == "SELL" and symbol in self._positions:
            buy_price = self._positions[symbol]["avg_price"]
            pnl = (fill_price - buy_price) * qty
            self._positions[symbol]["unrealized_pnl"] = pnl
            del self._positions[symbol]

        logger.info("Order placed: %s %s %s x%d @ %.2f (fill: %.2f)", order_id, side, symbol, qty, market_price, fill_price)
        return order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders and self._orders[order_id]["status"] == STATUS_OPEN:
            self._orders[order_id]["status"] = STATUS_CANCELLED
            return True
        return False

    def get_positions(self) -> list[dict[str, Any]]:
        return list(self._positions.values())

    def get_orders(self) -> list[dict[str, Any]]:
        return list(self._orders.values())

    def get_quote(self, symbol: str) -> dict[str, Any]:
        price = self._get_mock_price(symbol)
        spread = price * 0.001
        return {
            "symbol": symbol,
            "ltp": price,
            "bid": round(price - spread / 2, 2),
            "ask": round(price + spread / 2, 2),
            "spread_pct": round(spread / price * 100, 4),
            "volume": random.randint(100_000, 5_000_000),
            "avg_volume_20d": 2_000_000,
        }

    def is_connected(self) -> bool:
        return True

    def _get_mock_price(self, symbol: str) -> float:
        defaults = {
            "BEL": 412.0, "HAL": 3750.0, "RELIANCE": 2820.0,
            "INFY": 1760.0, "TCS": 4120.0, "BHEL": 258.0,
        }
        return defaults.get(symbol, 200.0 + (hash(symbol) % 1000))

"""Broker interface, mock implementation, and live connectors (Zerodha, Upstox).

Live connectors implement the same :class:`BrokerInterface` contract as
:class:`MockBroker`, so the rest of the platform is broker-agnostic. Select the
active broker via ``config/broker_config.yaml`` (``broker.provider``) and supply
credentials through environment variables (never in config files).

References:
  * Zerodha Kite Connect v3 — https://kite.trade/docs/connect/v3/
  * Upstox API v2          — https://upstox.com/developer/api-documentation/
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

ORDER_TYPE_MARKET = "MARKET"
ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_VWAP = "VWAP"

STATUS_OPEN = "OPEN"
STATUS_FILLED = "FILLED"
STATUS_COMPLETE = "COMPLETE"
STATUS_CANCELLED = "CANCELLED"
STATUS_REJECTED = "REJECTED"


# ── Errors ───────────────────────────────────────────────────────────────────
class BrokerError(Exception):
    """Base class for all broker failures."""


class BrokerAuthError(BrokerError):
    """Missing or invalid credentials."""


class BrokerOrderError(BrokerError):
    """Order placement/cancel rejected by the broker."""


class BrokerCircuitOpenError(BrokerError):
    """Circuit breaker is open — calls are being short-circuited."""


# ── Canonical schemas (Pydantic enforces tool-response shape) ─────────────────
class Order(BaseModel):
    """Normalised order representation returned by every broker."""

    order_id: str
    symbol: str
    qty: int
    side: str                      # BUY | SELL
    order_type: str = ORDER_TYPE_MARKET
    requested_price: float | None = None
    fill_price: float = 0.0
    slippage: float = 0.0
    status: str = STATUS_OPEN
    tag: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Quote(BaseModel):
    symbol: str
    ltp: float
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0


class BrokerInterface(ABC):
    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,  # BUY | SELL
        order_type: str = ORDER_TYPE_MARKET,
        price: float | None = None,
        tag: str | None = None,
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


# ── Resilient HTTP client: retry + exponential backoff + circuit breaker ──────
class _ResilientHttp:
    """Shared HTTP helper used by live connectors."""

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        cb_threshold: int = 5,
        cb_cooldown: float = 60.0,
    ):
        self._base = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(headers)
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._cb_threshold = cb_threshold
        self._cb_cooldown = cb_cooldown
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def _check_circuit(self) -> None:
        if self._consecutive_failures >= self._cb_threshold:
            if time.monotonic() < self._circuit_open_until:
                raise BrokerCircuitOpenError(
                    f"Circuit open after {self._consecutive_failures} failures; "
                    f"retry after {self._circuit_open_until - time.monotonic():.0f}s"
                )
            # Cooldown elapsed — half-open: allow one trial call
            self._consecutive_failures = self._cb_threshold - 1

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._cb_threshold:
            self._circuit_open_until = time.monotonic() + self._cb_cooldown
            logger.error("Broker circuit breaker tripped for %.0fs", self._cb_cooldown)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        self._check_circuit()
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._session.request(
                    method, url, params=params, data=data, json=json_body, timeout=self._timeout
                )
                if resp.status_code in (401, 403):
                    raise BrokerAuthError(f"{resp.status_code} from {path}: {resp.text[:200]}")
                if resp.status_code >= 500:
                    raise BrokerError(f"{resp.status_code} server error from {path}")
                payload = resp.json()
                self._record_success()
                return payload
            except BrokerAuthError:
                # Auth failures are not retryable
                self._record_failure()
                raise
            except (requests.RequestException, BrokerError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "Broker call %s %s failed (attempt %d/%d): %s",
                    method, path, attempt, self._max_retries, exc,
                )
                if attempt < self._max_retries:
                    time.sleep(self._backoff_base * (2 ** (attempt - 1)))
        self._record_failure()
        raise BrokerError(f"Broker call {method} {path} failed after {self._max_retries} attempts: {last_exc}")


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
        tag: str | None = None,
    ) -> dict[str, Any]:
        # Idempotency: a repeated tag returns the original order instead of a duplicate
        if tag:
            for existing in self._orders.values():
                if existing.get("tag") == tag:
                    logger.info("Mock idempotent hit for tag=%s -> %s", tag, existing["order_id"])
                    return existing

        order_id = str(uuid.uuid4())[:8].upper()
        market_price = price or self._get_mock_price(symbol)
        fill_price = self._simulate_fill_price(market_price, side)
        slippage = abs(fill_price - market_price)

        order = Order(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            requested_price=price,
            fill_price=fill_price,
            slippage=round(slippage, 4),
            status=STATUS_FILLED,
            tag=tag,
        ).model_dump()
        self._orders[order_id] = order

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


# ── Zerodha Kite Connect v3 ───────────────────────────────────────────────────
class ZerodhaBroker(BrokerInterface):
    """Live connector for Zerodha Kite Connect v3 (https://api.kite.trade).

    Auth: header ``Authorization: token {api_key}:{access_token}`` + ``X-Kite-Version: 3``.
    Credentials come from KITE_API_KEY and KITE_ACCESS_TOKEN env vars.
    """

    BASE_URL = "https://api.kite.trade"

    def __init__(self, cfg: Any):
        api_key = os.getenv("KITE_API_KEY")
        access_token = os.getenv("KITE_ACCESS_TOKEN")
        if not api_key or not access_token:
            raise BrokerAuthError("KITE_API_KEY and KITE_ACCESS_TOKEN must be set for Zerodha")
        self._exchange = cfg.exchange
        self._product = cfg.product            # MIS | CNC | NRML
        self._variety = cfg.variety            # regular | amo | co | iceberg
        self._http = _ResilientHttp(
            base_url=self.BASE_URL,
            headers={
                "X-Kite-Version": "3",
                "Authorization": f"token {api_key}:{access_token}",
            },
            timeout=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base_seconds,
            cb_threshold=cfg.circuit_breaker_threshold,
            cb_cooldown=cfg.circuit_breaker_cooldown_seconds,
        )

    @staticmethod
    def _check_status(payload: dict, action: str) -> dict:
        if payload.get("status") != "success":
            raise BrokerOrderError(f"Kite {action} failed: {payload}")
        return payload.get("data", {})

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = ORDER_TYPE_MARKET,
        price: float | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "tradingsymbol": symbol,
            "exchange": self._exchange,
            "transaction_type": side,                 # BUY | SELL
            "order_type": order_type,                 # MARKET | LIMIT | SL | SL-M
            "quantity": qty,
            "product": self._product,
            "validity": "DAY",
        }
        if order_type == ORDER_TYPE_LIMIT and price is not None:
            body["price"] = price
        if tag:
            body["tag"] = tag[:20]                     # Kite limits tag to 20 chars
        # POST /orders/{variety} — Kite expects form-encoded data
        payload = self._http.request("POST", f"/orders/{self._variety}", data=body)
        data = self._check_status(payload, "place_order")
        order_id = str(data.get("order_id", ""))
        return Order(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            requested_price=price,
            fill_price=price or 0.0,                   # actual fill resolved via get_orders polling
            status=STATUS_OPEN,
            tag=tag,
        ).model_dump()

    def cancel_order(self, order_id: str) -> bool:
        payload = self._http.request("DELETE", f"/orders/{self._variety}/{order_id}")
        return payload.get("status") == "success"

    def get_positions(self) -> list[dict[str, Any]]:
        payload = self._http.request("GET", "/portfolio/positions")
        data = self._check_status(payload, "get_positions")
        net = data.get("net", []) if isinstance(data, dict) else []
        return [
            {
                "symbol": p.get("tradingsymbol"),
                "qty": p.get("quantity", 0),
                "avg_price": p.get("average_price", 0.0),
                "current_price": p.get("last_price", 0.0),
                "unrealized_pnl": p.get("pnl", 0.0),
            }
            for p in net
        ]

    def get_orders(self) -> list[dict[str, Any]]:
        payload = self._http.request("GET", "/orders")
        data = self._check_status(payload, "get_orders")
        orders: list[dict] = []
        for o in data if isinstance(data, list) else []:
            try:
                orders.append(
                    Order(
                        order_id=str(o.get("order_id", "")),
                        symbol=o.get("tradingsymbol", ""),
                        qty=int(o.get("quantity", 0)),
                        side=o.get("transaction_type", "BUY"),
                        order_type=o.get("order_type", ORDER_TYPE_MARKET),
                        requested_price=o.get("price"),
                        fill_price=o.get("average_price", 0.0) or 0.0,
                        status=o.get("status", STATUS_OPEN),
                        tag=o.get("tag"),
                    ).model_dump()
                )
            except ValidationError as exc:
                logger.warning("Skipping malformed Kite order: %s", exc)
        return orders

    def get_quote(self, symbol: str) -> dict[str, Any]:
        instrument = f"{self._exchange}:{symbol}"
        payload = self._http.request("GET", "/quote/ltp", params={"i": instrument})
        data = self._check_status(payload, "get_quote")
        ltp = data.get(instrument, {}).get("last_price", 0.0) if isinstance(data, dict) else 0.0
        return Quote(symbol=symbol, ltp=ltp).model_dump()

    def is_connected(self) -> bool:
        try:
            self._http.request("GET", "/user/margins")
            return True
        except BrokerError:
            return False


# ── Upstox API v2 ─────────────────────────────────────────────────────────────
class UpstoxBroker(BrokerInterface):
    """Live connector for Upstox API v2 (https://api.upstox.com/v2).

    Auth: header ``Authorization: Bearer {access_token}`` from UPSTOX_ACCESS_TOKEN.
    Upstox addresses instruments by ``instrument_key`` (e.g. ``NSE_EQ|INE009A01021``),
    so a ``{SYMBOL: instrument_key}`` JSON map must be supplied
    (``broker.upstox_instrument_map``).
    """

    BASE_URL = "https://api.upstox.com/v2"
    # Upstox product codes: I = Intraday, D = Delivery
    _PRODUCT_MAP = {"MIS": "I", "CNC": "D", "NRML": "D"}

    def __init__(self, cfg: Any, config_root: Path | None = None):
        access_token = os.getenv("UPSTOX_ACCESS_TOKEN")
        if not access_token:
            raise BrokerAuthError("UPSTOX_ACCESS_TOKEN must be set for Upstox")
        self._product = self._PRODUCT_MAP.get(cfg.product, "I")
        self._instruments = self._load_instruments(cfg, config_root)
        self._http = _ResilientHttp(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
            backoff_base=cfg.backoff_base_seconds,
            cb_threshold=cfg.circuit_breaker_threshold,
            cb_cooldown=cfg.circuit_breaker_cooldown_seconds,
        )

    @staticmethod
    def _load_instruments(cfg: Any, config_root: Path | None) -> dict[str, str]:
        root = config_root or (Path(__file__).parent.parent.parent.parent / "config")
        path = root / cfg.upstox_instrument_map
        if not path.exists():
            logger.warning("Upstox instrument map not found at %s — quotes/orders will fail until provided", path)
            return {}
        with open(path) as f:
            return json.load(f)

    def _instrument_key(self, symbol: str) -> str:
        key = self._instruments.get(symbol)
        if not key:
            raise BrokerOrderError(f"No Upstox instrument_key mapped for symbol '{symbol}'")
        return key

    @staticmethod
    def _check_status(payload: dict, action: str) -> Any:
        if payload.get("status") != "success":
            raise BrokerOrderError(f"Upstox {action} failed: {payload}")
        return payload.get("data", {})

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = ORDER_TYPE_MARKET,
        price: float | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "quantity": qty,
            "product": self._product,
            "validity": "DAY",
            "price": price if (order_type == ORDER_TYPE_LIMIT and price is not None) else 0,
            "instrument_token": self._instrument_key(symbol),
            "order_type": order_type,
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }
        if tag:
            body["tag"] = tag[:40]
        payload = self._http.request("POST", "/order/place", json_body=body)
        data = self._check_status(payload, "place_order")
        order_id = str(data.get("order_id", ""))
        return Order(
            order_id=order_id,
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            requested_price=price,
            fill_price=price or 0.0,
            status=STATUS_OPEN,
            tag=tag,
        ).model_dump()

    def cancel_order(self, order_id: str) -> bool:
        payload = self._http.request("DELETE", "/order/cancel", params={"order_id": order_id})
        return payload.get("status") == "success"

    def get_positions(self) -> list[dict[str, Any]]:
        payload = self._http.request("GET", "/portfolio/short-term-positions")
        data = self._check_status(payload, "get_positions")
        return [
            {
                "symbol": p.get("trading_symbol") or p.get("tradingsymbol"),
                "qty": p.get("quantity", 0),
                "avg_price": p.get("average_price", 0.0),
                "current_price": p.get("last_price", 0.0),
                "unrealized_pnl": p.get("unrealised", p.get("pnl", 0.0)),
            }
            for p in (data if isinstance(data, list) else [])
        ]

    def get_orders(self) -> list[dict[str, Any]]:
        payload = self._http.request("GET", "/order/retrieve-all")
        data = self._check_status(payload, "get_orders")
        orders: list[dict] = []
        for o in data if isinstance(data, list) else []:
            try:
                orders.append(
                    Order(
                        order_id=str(o.get("order_id", "")),
                        symbol=o.get("trading_symbol") or o.get("tradingsymbol", ""),
                        qty=int(o.get("quantity", 0)),
                        side=o.get("transaction_type", "BUY"),
                        order_type=o.get("order_type", ORDER_TYPE_MARKET),
                        requested_price=o.get("price"),
                        fill_price=o.get("average_price", 0.0) or 0.0,
                        status=o.get("status", STATUS_OPEN),
                        tag=o.get("tag"),
                    ).model_dump()
                )
            except ValidationError as exc:
                logger.warning("Skipping malformed Upstox order: %s", exc)
        return orders

    def get_quote(self, symbol: str) -> dict[str, Any]:
        key = self._instrument_key(symbol)
        payload = self._http.request("GET", "/market-quote/ltp", params={"instrument_key": key})
        data = self._check_status(payload, "get_quote")
        ltp = 0.0
        if isinstance(data, dict):
            # Upstox keys the response by a normalised symbol; take the first entry's last_price
            for entry in data.values():
                ltp = entry.get("last_price", 0.0)
                break
        return Quote(symbol=symbol, ltp=ltp).model_dump()

    def is_connected(self) -> bool:
        try:
            self._http.request("GET", "/user/profile")
            return True
        except BrokerError:
            return False


# ── Factory ───────────────────────────────────────────────────────────────────
def get_broker(cfg: Any | None = None) -> BrokerInterface:
    """Return the configured broker. ``cfg`` is a ``BrokerConfig`` (or None -> mock).

    provider: mock | zerodha | upstox
    """
    if cfg is None:
        return MockBroker()
    provider = (cfg.provider or "mock").lower()
    if provider == "mock":
        return MockBroker(slippage_bps=cfg.slippage_bps)
    if provider == "zerodha":
        return ZerodhaBroker(cfg)
    if provider == "upstox":
        return UpstoxBroker(cfg)
    raise ValueError(f"Unknown broker provider '{provider}' (expected mock|zerodha|upstox)")

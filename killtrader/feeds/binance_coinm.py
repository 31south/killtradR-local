from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from time import time_ns
from typing import Any, Literal

import httpx
import websockets
from pydantic import BaseModel, Field, ValidationError, field_validator

from killtrader.config import Settings
from killtrader.core.bus import (
    BinanceCoinMAggTradeEvent,
    BinanceCoinMForceOrderEvent,
    Candle,
    EventBus,
    OrderBookLevel,
    OrderBookSnapshot,
)
from killtrader.core.errors import NoDataAvailableError, SourceUnavailableError
from killtrader.core.logger import get_logger

log = get_logger(__name__)

COINM_REST_BASE = "https://dapi.binance.com"


class AggTradePayload(BaseModel):
    event_type: Literal["aggTrade"] = Field(alias="e")
    event_time_ms: int = Field(alias="E")
    trade_id: int = Field(alias="a")
    symbol: str = Field(alias="s")
    price: float = Field(alias="p")
    quantity: float = Field(alias="q")
    first_trade_id: int = Field(alias="f")
    last_trade_id: int = Field(alias="l")
    trade_time_ms: int = Field(alias="T")
    is_buyer_maker: bool = Field(alias="m")

    @field_validator("price", "quantity", mode="before")
    @classmethod
    def parse_float(cls, value: Any) -> float:
        return float(value)

    def to_event(self) -> BinanceCoinMAggTradeEvent:
        return BinanceCoinMAggTradeEvent(
            source="binance_coinm",
            symbol=self.symbol,
            event_time_ms=self.event_time_ms,
            trade_id=self.trade_id,
            price=self.price,
            quantity=self.quantity,
            first_trade_id=self.first_trade_id,
            last_trade_id=self.last_trade_id,
            trade_time_ms=self.trade_time_ms,
            is_buyer_maker=self.is_buyer_maker,
        )


class ForceOrderInnerPayload(BaseModel):
    symbol: str = Field(alias="s")
    side: Literal["BUY", "SELL"] = Field(alias="S")
    quantity: float = Field(alias="q")
    price: float = Field(alias="p")
    avg_price: float = Field(alias="ap")
    order_status: str = Field(alias="X")
    order_last_filled_qty: float = Field(alias="l")
    order_filled_accumulated_qty: float = Field(alias="z")
    order_trade_time_ms: int = Field(alias="T")

    @field_validator(
        "quantity",
        "price",
        "avg_price",
        "order_last_filled_qty",
        "order_filled_accumulated_qty",
        mode="before",
    )
    @classmethod
    def parse_float(cls, value: Any) -> float:
        return float(value)


class ForceOrderPayload(BaseModel):
    event_type: Literal["forceOrder"] = Field(alias="e")
    event_time_ms: int = Field(alias="E")
    order: ForceOrderInnerPayload = Field(alias="o")

    def to_event(self, contract_value_usd: float) -> BinanceCoinMForceOrderEvent:
        filled_qty = self.order.order_filled_accumulated_qty or self.order.quantity
        return BinanceCoinMForceOrderEvent(
            source="binance_coinm",
            symbol=self.order.symbol,
            event_time_ms=self.event_time_ms,
            side=self.order.side,
            price=self.order.price,
            quantity=self.order.quantity,
            avg_price=self.order.avg_price,
            order_status=self.order.order_status,
            order_last_filled_qty=self.order.order_last_filled_qty,
            order_filled_accumulated_qty=self.order.order_filled_accumulated_qty,
            order_trade_time_ms=self.order.order_trade_time_ms,
            notional_usd=abs(filled_qty * contract_value_usd),
        )


class BinanceCoinMFeed:
    """Real Binance Delivery Futures source for COIN-M aggregate trades and liquidations."""

    def __init__(self, settings: Settings, bus: EventBus | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self._client = httpx.AsyncClient(base_url=COINM_REST_BASE, timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BinanceCoinMFeed:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def run(self) -> None:
        consecutive_parse_errors = 0
        backoff_seconds = 1.0
        while True:
            try:
                await self._stream_once()
                backoff_seconds = 1.0
                consecutive_parse_errors = 0
            except asyncio.CancelledError:
                raise
            except ValidationError as exc:
                consecutive_parse_errors += 1
                log.warning(
                    "binance_coinm_parse_failed",
                    consecutive=consecutive_parse_errors,
                    error=str(exc),
                )
                if consecutive_parse_errors > 10:
                    raise NoDataAvailableError(
                        "Binance COIN-M websocket payloads failed parsing repeatedly"
                    ) from exc
            except Exception as exc:
                log.warning(
                    "binance_coinm_disconnected",
                    reconnect_in_sec=backoff_seconds,
                    error=str(exc),
                )
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _stream_once(self) -> None:
        subscribe_payload = {
            "method": "SUBSCRIBE",
            "params": [
                f"{self.settings.binance_coinm_symbol}@aggTrade",
                f"{self.settings.binance_coinm_symbol}@forceOrder",
            ],
            "id": 1,
        }
        log.info(
            "binance_coinm_connecting",
            url=self.settings.binance_coinm_ws_url,
            symbol=self.settings.binance_coinm_symbol,
        )
        async with websockets.connect(self.settings.binance_coinm_ws_url, ping_interval=None) as ws:
            await ws.send(json.dumps(subscribe_payload))
            log.info("binance_coinm_subscribed", payload=subscribe_payload)
            async for raw in ws:
                await self._handle_raw_message(raw)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        payload = json.loads(raw)
        if payload.get("result") is None and payload.get("id") == 1:
            log.info("binance_coinm_subscription_ack")
            return
        if payload.get("e") == "aggTrade":
            event = parse_agg_trade(payload)
            if self.bus is not None:
                await self.bus.publish_coinm_agg_trade(event)
            return
        if payload.get("e") == "forceOrder":
            event = parse_force_order(payload, self.settings.binance_coinm_contract_value_usd)
            if self.bus is not None:
                await self.bus.publish_coinm_force_order(event)
            return
        log.debug("binance_coinm_ignored_message", payload=payload)

    async def stream_liquidations(self) -> AsyncIterator[BinanceCoinMForceOrderEvent]:
        queue_bus = EventBus()
        feeder = BinanceCoinMFeed(self.settings, queue_bus)
        task = asyncio.create_task(feeder.run())
        try:
            while True:
                yield await queue_bus.coinm_force_orders.get()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await feeder.close()

    async def fetch_order_book_once(self) -> OrderBookSnapshot:
        symbol = self.settings.binance_coinm_symbol.upper()
        try:
            response = await self._client.get(
                "/dapi/v1/depth", params={"symbol": symbol, "limit": 50}
            )
            response.raise_for_status()
            raw = response.json()
        except Exception as exc:
            raise SourceUnavailableError("Binance COIN-M order book fetch failed") from exc
        bids = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("bids", [])]
        asks = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("asks", [])]
        if not bids or not asks:
            raise SourceUnavailableError("Binance COIN-M order book was empty")
        return OrderBookSnapshot(
            source="binance_coinm",
            symbol=symbol,
            timestamp_ms=time_ns() // 1_000_000,
            bids=bids,
            asks=asks,
        )

    async def fetch_candles_once(self, timeframe: str = "1m", limit: int = 120) -> list[Candle]:
        symbol = self.settings.binance_coinm_symbol.upper()
        try:
            response = await self._client.get(
                "/dapi/v1/klines",
                params={"symbol": symbol, "interval": timeframe, "limit": limit},
            )
            response.raise_for_status()
            rows = response.json()
        except Exception as exc:
            raise SourceUnavailableError("Binance COIN-M OHLCV fetch failed") from exc
        if not rows:
            raise SourceUnavailableError("Binance COIN-M OHLCV response was empty")
        return [
            Candle(
                source="binance_coinm",
                symbol=symbol,
                timestamp_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]


def parse_agg_trade(payload: dict[str, Any]) -> BinanceCoinMAggTradeEvent:
    return AggTradePayload.model_validate(payload).to_event()


def parse_force_order(
    payload: dict[str, Any], contract_value_usd: float = 100.0
) -> BinanceCoinMForceOrderEvent:
    return ForceOrderPayload.model_validate(payload).to_event(contract_value_usd)

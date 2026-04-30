from __future__ import annotations

import asyncio
import json
from time import time_ns
from typing import AsyncIterator

import websockets

from killtrader.config import Settings
from killtrader.core.bus import OrderBookLevel, OrderBookSnapshot
from killtrader.core.errors import SourceUnavailableError
from killtrader.core.logger import get_logger

log = get_logger(__name__)


class AggrTradeFeed:
    """Best-effort adapter for the public aggr.trade websocket endpoint.

    The URL is configurable because aggr.trade has changed client endpoints over
    time. Incompatible payloads raise a real source error rather than inventing
    an order book.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def stream_order_book(self) -> AsyncIterator[OrderBookSnapshot]:
        try:
            async with websockets.connect(self.settings.aggr_trade_ws_url, ping_interval=20) as websocket:
                await websocket.send(json.dumps({"op": "subscribe", "channel": "orderbook", "symbol": self.settings.symbol}))
                async for raw in websocket:
                    parsed = json.loads(raw)
                    snapshot = self._parse_payload(parsed)
                    if snapshot:
                        yield snapshot
        except Exception as exc:
            raise SourceUnavailableError("aggr.trade websocket source failed") from exc

    def _parse_payload(self, payload: dict) -> OrderBookSnapshot | None:
        bids_raw = payload.get("bids") or payload.get("b")
        asks_raw = payload.get("asks") or payload.get("a")
        if not bids_raw or not asks_raw:
            return None
        bids = [OrderBookLevel(float(level[0]), float(level[1])) for level in bids_raw]
        asks = [OrderBookLevel(float(level[0]), float(level[1])) for level in asks_raw]
        return OrderBookSnapshot(
            source="aggr.trade",
            symbol=str(payload.get("symbol") or self.settings.symbol),
            timestamp_ms=int(payload.get("timestamp") or payload.get("ts") or time_ns() // 1_000_000),
            bids=bids,
            asks=asks,
        )

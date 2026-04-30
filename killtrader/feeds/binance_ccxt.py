from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from time import time_ns

from killtrader.config import Settings
from killtrader.core.bus import Candle, OrderBookLevel, OrderBookSnapshot
from killtrader.core.errors import SourceUnavailableError
from killtrader.core.logger import get_logger

log = get_logger(__name__)


class BinancePerpFeed:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._exchange = None

    async def connect(self) -> None:
        try:
            import ccxt.async_support as ccxt  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise SourceUnavailableError(
                "ccxt is not importable; install project dependencies"
            ) from exc
        self._exchange = ccxt.binanceusdm(
            {"enableRateLimit": True, "options": {"defaultType": "future"}}
        )
        log.info("binance_feed_connected", symbol=self.settings.binance_symbol)

    async def close(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()

    async def fetch_order_book_once(self) -> OrderBookSnapshot:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        try:
            raw = await self._exchange.fetch_order_book(self.settings.binance_symbol, limit=50)
        except Exception as exc:
            raise SourceUnavailableError("Binance perps order book fetch failed") from exc
        bids = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("bids", [])]
        asks = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("asks", [])]
        if not bids or not asks:
            raise SourceUnavailableError("Binance perps order book was empty")
        return OrderBookSnapshot(
            source="binance",
            symbol=self.settings.binance_symbol,
            timestamp_ms=int(raw.get("timestamp") or time_ns() // 1_000_000),
            bids=bids,
            asks=asks,
        )

    async def fetch_candles_once(self, timeframe: str = "1m", limit: int = 120) -> list[Candle]:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        try:
            rows = await self._exchange.fetch_ohlcv(
                self.settings.binance_symbol, timeframe=timeframe, limit=limit
            )
        except Exception as exc:
            raise SourceUnavailableError("Binance perps OHLCV fetch failed") from exc
        if not rows:
            raise SourceUnavailableError("Binance perps OHLCV response was empty")
        return [
            Candle(
                source="binance",
                symbol=self.settings.binance_symbol,
                timestamp_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]

    async def stream_order_book(
        self, poll_seconds: float = 1.0
    ) -> AsyncIterator[OrderBookSnapshot]:
        while True:
            yield await self.fetch_order_book_once()
            await asyncio.sleep(poll_seconds)

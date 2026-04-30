from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from time import time_ns

from killtrader.config import Settings
from killtrader.core.bus import Candle, ChokeAlert, EventBus, OrderBookSnapshot
from killtrader.core.errors import NoDataAvailableError, SourceUnavailableError
from killtrader.core.logger import get_logger
from killtrader.exchange.blofin import BloFinMarketFeed
from killtrader.feeds.aggr_trade import AggrTradeFeed
from killtrader.feeds.binance_ccxt import BinancePerpFeed

log = get_logger(__name__)


class FeedState(str, Enum):
    BLOFIN_PRIMARY = "BloFin-primary"
    BINANCE_FALLBACK = "Binance-fallback"
    AGGR_FALLBACK = "aggr.trade-fallback"
    HALTED = "HALTED"


class CrossReferenceCoordinator:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        blofin_fetch: Callable[[], Awaitable[OrderBookSnapshot]] | None = None,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.blofin_fetch = blofin_fetch
        self.blofin_market = BloFinMarketFeed(settings)
        self.binance = BinancePerpFeed(settings)
        self.aggr = AggrTradeFeed(settings)
        self.state = FeedState.BLOFIN_PRIMARY
        self.latency_window: deque[float] = deque(maxlen=120)
        self.last_alerts: deque[str] = deque(maxlen=100)

    async def next_snapshot(self) -> OrderBookSnapshot:
        source_call = self.blofin_fetch or self.blofin_market.fetch_order_book_once
        if source_call is not None:
            try:
                started = time_ns()
                snapshot = await source_call()
                latency_ms = (time_ns() - started) / 1_000_000
                self.latency_window.append(latency_ms)
                if latency_ms <= self.settings.choke_threshold_ms:
                    self.state = FeedState.BLOFIN_PRIMARY
                    return snapshot
                await self._alert(
                    latency_ms,
                    "[CHOKE ALERT] BloFin latency exceeded threshold; switching signal feed",
                )
            except SourceUnavailableError as exc:
                await self._alert(float("inf"), f"[CHOKE ALERT] BloFin source unavailable: {exc}")

        try:
            snapshot = await self.binance.fetch_order_book_once()
            self.state = FeedState.BINANCE_FALLBACK
            return snapshot
        except SourceUnavailableError as binance_error:
            log.warning("binance_source_failed", error=str(binance_error))

        try:
            stream = self.aggr.stream_order_book()
            snapshot = await asyncio.wait_for(anext(stream), timeout=10)
            self.state = FeedState.AGGR_FALLBACK
            return snapshot
        except Exception as aggr_error:
            self.state = FeedState.HALTED
            raise NoDataAvailableError(
                "all configured real market data sources failed; trading halted"
            ) from aggr_error

    async def next_candles(self) -> list[Candle]:
        try:
            candles = await self.blofin_market.fetch_candles_once(limit=120)
            self.state = FeedState.BLOFIN_PRIMARY
            return candles
        except SourceUnavailableError as blofin_error:
            await self._alert(
                float("inf"), f"[CHOKE ALERT] BloFin OHLCV unavailable: {blofin_error}"
            )
        try:
            candles = await self.binance.fetch_candles_once(limit=120)
            self.state = FeedState.BINANCE_FALLBACK
            return candles
        except SourceUnavailableError as binance_error:
            self.state = FeedState.HALTED
            raise NoDataAvailableError(
                "all configured real OHLCV sources failed; trading halted"
            ) from binance_error

    async def _alert(self, latency_ms: float, message: str) -> None:
        self.last_alerts.append(message)
        log.warning("choke_alert", latency_ms=latency_ms, message=message)
        await self.bus.publish_choke_alert(
            ChokeAlert(active_source=self.state.value, latency_ms=latency_ms, message=message)
        )

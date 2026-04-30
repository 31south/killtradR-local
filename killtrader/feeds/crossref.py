from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from enum import Enum
from time import time_ns

from killtrader.config import Settings
from killtrader.core.bus import Candle, ChokeAlert, EventBus, OrderBookSnapshot
from killtrader.core.errors import NoDataAvailableError, SourceUnavailableError
from killtrader.core.logger import get_logger
from killtrader.exchange.blofin import BloFinMarketFeed
from killtrader.feeds.binance_ccxt import BinancePerpFeed
from killtrader.feeds.binance_coinm import BinanceCoinMFeed

log = get_logger(__name__)


class FeedState(str, Enum):
    BLOFIN_PRIMARY = "BloFin-primary"
    BINANCE_USDTM_FALLBACK = "Binance-USDT-M-fallback"
    BINANCE_COINM_FALLBACK = "Binance-COIN-M-fallback"
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
        self.binance_coinm = BinanceCoinMFeed(settings, bus)
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
            self.state = FeedState.BINANCE_USDTM_FALLBACK
            return snapshot
        except SourceUnavailableError as binance_error:
            log.warning("binance_usdtm_source_failed", error=str(binance_error))

        try:
            snapshot = await self.binance_coinm.fetch_order_book_once()
            self.state = FeedState.BINANCE_COINM_FALLBACK
            await self._maybe_log_coinm_basis(snapshot)
            return snapshot
        except SourceUnavailableError as coinm_error:
            self.state = FeedState.HALTED
            raise NoDataAvailableError(
                "all configured real market data sources failed; trading halted"
            ) from coinm_error

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
            self.state = FeedState.BINANCE_USDTM_FALLBACK
            return candles
        except SourceUnavailableError as binance_error:
            log.warning("binance_usdtm_ohlcv_failed", error=str(binance_error))

        try:
            candles = await self.binance_coinm.fetch_candles_once(limit=120)
            self.state = FeedState.BINANCE_COINM_FALLBACK
            return candles
        except SourceUnavailableError as coinm_error:
            self.state = FeedState.HALTED
            raise NoDataAvailableError(
                "all configured real OHLCV sources failed; trading halted"
            ) from coinm_error

    async def _maybe_log_coinm_basis(self, coinm_snapshot: OrderBookSnapshot) -> None:
        try:
            blofin_snapshot = await self.blofin_market.fetch_order_book_once()
        except SourceUnavailableError:
            return
        if not blofin_snapshot.bids or not blofin_snapshot.asks:
            return
        if not coinm_snapshot.bids or not coinm_snapshot.asks:
            return
        blofin_mid = (blofin_snapshot.bids[0].price + blofin_snapshot.asks[0].price) / 2
        coinm_mid = (coinm_snapshot.bids[0].price + coinm_snapshot.asks[0].price) / 2
        if blofin_mid <= 0:
            return
        spread_bps = abs(coinm_mid - blofin_mid) / blofin_mid * 10_000
        log.info(
            "binance_coinm_basis_spread",
            blofin_mid=blofin_mid,
            coinm_mid=coinm_mid,
            spread_bps=spread_bps,
            note="COIN-M is inverse and BTC-settled; basis is expected within normal ranges",
        )
        if spread_bps >= self.settings.coinm_spread_alert_bps:
            await self._alert(
                spread_bps,
                (
                    "[COIN-M SPREAD] Binance inverse perp basis exceeded threshold; "
                    "watch cross-market stress before treating it as a choke"
                ),
            )

    async def _alert(self, latency_ms: float, message: str) -> None:
        self.last_alerts.append(message)
        log.warning("choke_alert", latency_ms=latency_ms, message=message)
        await self.bus.publish_choke_alert(
            ChokeAlert(active_source=self.state.value, latency_ms=latency_ms, message=message)
        )

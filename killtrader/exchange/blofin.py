from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from importlib import metadata
from time import time_ns
from typing import Any

from killtrader.config import Settings
from killtrader.core.bus import Candle, OrderBookLevel, OrderBookSnapshot
from killtrader.core.errors import (
    DemoNotSupportedError,
    ExchangeExecutionError,
    SourceUnavailableError,
)
from killtrader.core.logger import get_logger

log = get_logger(__name__)


def ccxt_symbol(symbol: str) -> str:
    if "/" in symbol:
        return symbol
    base, quote = symbol.split("-", 1)
    return f"{base}/{quote}:{quote}"


class BloFinMarketFeed:
    """Real public BloFin market data through ccxt async REST polling."""

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
        self._exchange = ccxt.blofin({"enableRateLimit": True, "options": {"defaultType": "swap"}})

    async def close(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    async def __aenter__(self) -> BloFinMarketFeed:
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def fetch_order_book_once(self) -> OrderBookSnapshot:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        symbol = ccxt_symbol(self.settings.symbol)
        try:
            raw = await self._exchange.fetch_order_book(symbol, limit=50)
        except Exception as exc:
            raise SourceUnavailableError("BloFin public order book fetch failed") from exc
        bids = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("bids", [])]
        asks = [OrderBookLevel(float(price), float(size)) for price, size in raw.get("asks", [])]
        if not bids or not asks:
            raise SourceUnavailableError("BloFin public order book was empty")
        return OrderBookSnapshot(
            source="blofin",
            symbol=self.settings.symbol,
            timestamp_ms=int(raw.get("timestamp") or time_ns() // 1_000_000),
            bids=bids,
            asks=asks,
        )

    async def fetch_candles_once(self, timeframe: str = "1m", limit: int = 120) -> list[Candle]:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        symbol = ccxt_symbol(self.settings.symbol)
        try:
            rows = await self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        except Exception as exc:
            raise SourceUnavailableError("BloFin public OHLCV fetch failed") from exc
        if not rows:
            raise SourceUnavailableError("BloFin public OHLCV response was empty")
        return [
            Candle(
                source="blofin",
                symbol=self.settings.symbol,
                timestamp_ms=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]


class BloFinExchange:
    """Thin wrapper around BloFin's official Python SDK.

    SDK imports are delayed so local syntax checks and CLI help do not require
    exchange credentials or network access.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any | None = None
        self._market_api: Any | None = None
        self._trading_api: Any | None = None

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in {
                "BLOFIN_API_KEY": self.settings.blofin_api_key,
                "BLOFIN_SECRET": self.settings.blofin_secret,
                "BLOFIN_PASSPHRASE": self.settings.blofin_passphrase,
            }.items()
            if not value
        ]
        if missing:
            raise ExchangeExecutionError(f"missing BloFin credentials: {', '.join(missing)}")

    async def connect(self) -> None:
        self._require_credentials()
        if self.settings.use_demo:
            validate_demo_mode_supported(self.settings)
        try:
            import blofin.client as blofin_client  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on user install
            raise ExchangeExecutionError(
                "blofin package is not importable; install project dependencies"
            ) from exc

        client_cls = (
            blofin_client.DemoClient if self.settings.use_demo else blofin_client.BloFinClient
        )

        self._client = client_cls(
            api_key=self.settings.blofin_api_key,
            api_secret=self.settings.blofin_secret,
            passphrase=self.settings.blofin_passphrase,
            use_server_time=True,
        )
        self._market_api = blofin_client.PublicAPI(self._client)
        self._trading_api = blofin_client.TradingAPI(self._client)
        log.info("blofin_connected", demo=self.settings.use_demo, symbol=self.settings.symbol)

    async def close(self) -> None:
        client = self._client
        close = getattr(client, "close", None) if client is not None else None
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
        self._client = None
        self._market_api = None
        self._trading_api = None

    async def __aenter__(self) -> BloFinExchange:
        await self.connect()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def _call(self, func_name: str, *args: Any, **kwargs: Any) -> Any:
        if self._trading_api is None:
            await self.connect()
        assert self._trading_api is not None
        func = getattr(self._trading_api, func_name, None)
        if func is None:
            raise ExchangeExecutionError(f"BloFin SDK missing expected method: {func_name}")
        for attempt in range(3):
            try:
                result = func(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:
                if attempt == 2:
                    raise ExchangeExecutionError(
                        f"BloFin {func_name} failed after retries"
                    ) from exc
                await asyncio.sleep(0.5 * (attempt + 1))

    async def place_market_order(self, symbol: str, side: str, size: float) -> Any:
        return await self._call(
            "place_order", instId=symbol, tdMode="cross", side=side, ordType="market", sz=str(size)
        )

    async def place_limit_order(self, symbol: str, side: str, size: float, price: float) -> Any:
        return await self._call(
            "place_order",
            instId=symbol,
            tdMode="cross",
            side=side,
            ordType="limit",
            sz=str(size),
            px=str(price),
        )

    async def set_tp_sl(
        self, symbol: str, side: str, size: float, stop: float, tp1: float, tp2: float
    ) -> Any:
        return await self._call(
            "place_algo_order",
            instId=symbol,
            tdMode="cross",
            side=side,
            sz=str(size),
            slTriggerPx=str(stop),
            tpTriggerPx=str(tp1),
            attachAlgoOrds=[{"tpTriggerPx": str(tp2), "sz": str(size / 2)}],
        )

    async def close_position(self, symbol: str, side: str, size: float) -> Any:
        close_side = "sell" if side == "long" else "buy"
        return await self.place_market_order(symbol, close_side, size)

    async def cancel_order(self, symbol: str, order_id: str) -> Any:
        return await self._call("cancel_order", instId=symbol, ordId=order_id)

    async def stream_candles(self, symbol: str) -> AsyncIterator[Candle]:
        raise SourceUnavailableError(
            "BloFin websocket adapter must be wired to the installed blofin SDK "
            "version before streaming candles"
        )

    async def stream_order_book(self, symbol: str) -> AsyncIterator[OrderBookSnapshot]:
        raise SourceUnavailableError(
            "BloFin websocket adapter must be wired to the installed blofin SDK "
            "version before streaming order books"
        )


def order_book_from_raw(
    source: str, symbol: str, timestamp_ms: int, raw: dict[str, Any]
) -> OrderBookSnapshot:
    bids = [
        OrderBookLevel(price=float(price), size=float(size))
        for price, size, *_ in raw.get("bids", [])
    ]
    asks = [
        OrderBookLevel(price=float(price), size=float(size))
        for price, size, *_ in raw.get("asks", [])
    ]
    if not bids or not asks:
        raise SourceUnavailableError(f"{source} returned an empty order book for {symbol}")
    return OrderBookSnapshot(
        source=source, symbol=symbol, timestamp_ms=timestamp_ms, bids=bids, asks=asks
    )


def blofin_sdk_version() -> str:
    try:
        return metadata.version("blofin")
    except metadata.PackageNotFoundError:
        return "not installed"


def validate_demo_mode_supported(settings: Settings) -> None:
    if not settings.use_demo:
        return
    version = blofin_sdk_version()
    try:
        import blofin.client as blofin_client  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on user install
        raise DemoNotSupportedError(
            "USE_DEMO=true is not supported because the blofin package is not importable. "
            f"Installed blofin SDK version: {version}."
        ) from exc
    if not hasattr(blofin_client, "DemoClient"):
        raise DemoNotSupportedError(
            "USE_DEMO=true is not supported with installed blofin SDK version "
            f"{version}. This SDK does not expose a DemoClient."
        )

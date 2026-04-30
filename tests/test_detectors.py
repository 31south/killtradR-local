from __future__ import annotations

import asyncio
import json
from pathlib import Path

from killtrader.config import Settings
from killtrader.core.bus import Candle, EventBus, OrderBookLevel, OrderBookSnapshot
from killtrader.detectors.liquidity_grab import LiquidityGrabDetector
from killtrader.detectors.order_book_imbalance import OrderBookImbalanceDetector


def _load_real_candles() -> list[Candle]:
    payload = json.loads(Path("tests/fixtures/kraken_xbtusdt_ohlc_real_2026_04_29.json").read_text())
    candles = []
    for row in payload["candles"]:
        candles.append(
            Candle(
                source="kraken",
                symbol="XBTUSDT",
                timestamp_ms=int(row[0]) * 1000,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[6]),
            )
        )
    return candles


def test_liquidity_grab_detector_accepts_recorded_real_candles() -> None:
    async def scenario() -> None:
        settings = Settings(LIQUIDITY_GRAB_LOOKBACK_BARS=10)
        bus = EventBus()
        detector = LiquidityGrabDetector(settings, bus)
        for candle in _load_real_candles():
            await detector.on_candle(candle)
        assert detector.candles

    asyncio.run(scenario())


def test_order_book_imbalance_uses_real_snapshot_shape() -> None:
    async def scenario() -> None:
        settings = Settings()
        bus = EventBus()
        detector = OrderBookImbalanceDetector(settings, bus)
        payload = json.loads(Path("tests/fixtures/kraken_xbtusdt_depth_real_2026_04_29.json").read_text())
        snapshot = OrderBookSnapshot(
            source="kraken",
            symbol="XBTUSDT",
            timestamp_ms=max(int(level[2]) for level in payload["asks"] + payload["bids"]) * 1000,
            bids=[OrderBookLevel(price=float(level[0]), size=float(level[1])) for level in payload["bids"]],
            asks=[OrderBookLevel(price=float(level[0]), size=float(level[1])) for level in payload["asks"]],
        )
        await detector.on_order_book(snapshot)
        assert detector.imbalances

    asyncio.run(scenario())

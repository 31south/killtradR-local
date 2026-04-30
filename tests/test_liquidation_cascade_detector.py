from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from killtrader.config import Settings
from killtrader.core.bus import EventBus
from killtrader.detectors.liquidation_cascade import LiquidationCascadeDetector
from killtrader.feeds.binance_coinm import parse_force_order


def test_liquidation_cascade_detector_with_recorded_real_force_orders_if_available() -> None:
    fixture = Path("tests/fixtures/binance_coinm_real_forceorder_2026_04_30.json")
    if not fixture.exists():
        pytest.skip("No recorded Binance COIN-M force-order messages captured in sandbox window")

    async def scenario() -> None:
        settings = Settings(
            LIQUIDATION_CASCADE_WINDOW_SEC=60,
            LIQUIDATION_CASCADE_USD_THRESHOLD=1,
            LIQUIDATION_CASCADE_COUNT_THRESHOLD=1,
            LIQUIDATION_CASCADE_DIRECTIONAL_BIAS=0.51,
        )
        bus = EventBus()
        detector = LiquidationCascadeDetector(settings, bus)
        payload = json.loads(fixture.read_text())
        for message in payload["messages"]:
            await detector.on_force_order(parse_force_order(message, contract_value_usd=100.0))
        assert bus.detector_events.qsize() >= 1

    asyncio.run(scenario())

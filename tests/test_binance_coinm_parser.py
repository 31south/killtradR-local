from __future__ import annotations

import json
from pathlib import Path

import pytest

from killtrader.feeds.binance_coinm import parse_agg_trade, parse_force_order


def test_parse_recorded_real_coinm_agg_trades() -> None:
    payload = json.loads(
        Path("tests/fixtures/binance_coinm_real_aggtrade_2026_04_30.json").read_text()
    )
    events = [parse_agg_trade(message) for message in payload["messages"]]
    assert events
    assert {event.source for event in events} == {"binance_coinm"}
    assert all(event.symbol == "BTCUSD_PERP" for event in events)
    assert all(event.price > 0 for event in events)
    assert all(event.quantity > 0 for event in events)


def test_parse_recorded_real_coinm_force_orders_if_available() -> None:
    fixture = Path("tests/fixtures/binance_coinm_real_forceorder_2026_04_30.json")
    if not fixture.exists():
        pytest.skip("No recorded Binance COIN-M force-order messages captured in sandbox window")
    payload = json.loads(fixture.read_text())
    events = [
        parse_force_order(message, contract_value_usd=100.0) for message in payload["messages"]
    ]
    assert events
    assert all(event.source == "binance_coinm" for event in events)
    assert all(event.notional_usd > 0 for event in events)

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Any, Literal


Side = Literal["long", "short"]


@dataclass(slots=True)
class Candle:
    source: str
    symbol: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(slots=True)
class OrderBookSnapshot:
    source: str
    symbol: str
    timestamp_ms: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


@dataclass(slots=True)
class DetectorEvent:
    detector: str
    symbol: str
    side: Side
    confidence: float
    trigger_price: float
    source: str
    thesis: str
    features: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time)


@dataclass(slots=True)
class ChokeAlert:
    active_source: str
    latency_ms: float
    message: str
    created_at: float = field(default_factory=time)


class EventBus:
    def __init__(self) -> None:
        self.detector_events: asyncio.Queue[DetectorEvent] = asyncio.Queue()
        self.choke_alerts: asyncio.Queue[ChokeAlert] = asyncio.Queue()

    async def publish_detector_event(self, event: DetectorEvent) -> None:
        await self.detector_events.put(event)

    async def publish_choke_alert(self, alert: ChokeAlert) -> None:
        await self.choke_alerts.put(alert)

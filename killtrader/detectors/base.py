from __future__ import annotations

from abc import ABC, abstractmethod

from killtrader.config import Settings
from killtrader.core.bus import Candle, EventBus, OrderBookSnapshot


class Detector(ABC):
    name: str

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self.settings = settings
        self.bus = bus

    async def on_candle(self, candle: Candle) -> None:
        return None

    async def on_order_book(self, order_book: OrderBookSnapshot) -> None:
        return None

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

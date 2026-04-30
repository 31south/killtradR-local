from __future__ import annotations

from collections import defaultdict, deque

from killtrader.config import Settings
from killtrader.core.bus import DetectorEvent, EventBus, OrderBookSnapshot
from killtrader.detectors.base import Detector


class OrderBookImbalanceDetector(Detector):
    name = "OrderBookImbalanceDetector"

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        super().__init__(settings, bus)
        self.imbalances: deque[float] = deque(maxlen=30)
        self.level_refills: dict[float, int] = defaultdict(int)
        self.last_sizes: dict[float, float] = {}

    def reset(self) -> None:
        self.imbalances.clear()
        self.level_refills.clear()
        self.last_sizes.clear()

    async def on_order_book(self, order_book: OrderBookSnapshot) -> None:
        bid_volume = sum(level.size for level in order_book.bids[:20])
        ask_volume = sum(level.size for level in order_book.asks[:20])
        total = bid_volume + ask_volume
        if total <= 0:
            return
        imbalance = (bid_volume - ask_volume) / total
        self.imbalances.append(imbalance)
        best_bid = order_book.bids[0].price
        best_ask = order_book.asks[0].price
        mid = (best_bid + best_ask) / 2
        refill_score = self._track_refills(order_book)
        rolling = sum(self.imbalances) / len(self.imbalances)

        if rolling > 0.38 and refill_score >= 3:
            await self.bus.publish_detector_event(
                DetectorEvent(
                    detector=self.name,
                    symbol=order_book.symbol,
                    side="long",
                    confidence=min(0.96, 0.66 + abs(rolling) * 0.55 + refill_score * 0.025),
                    trigger_price=mid,
                    source=order_book.source,
                    thesis=(
                        "bid-side absorption keeps refilling; sellers are punching "
                        "a wall and getting trapped"
                    ),
                    features={"rolling_imbalance": rolling, "refill_score": refill_score},
                )
            )
        elif rolling < -0.38 and refill_score >= 3:
            await self.bus.publish_detector_event(
                DetectorEvent(
                    detector=self.name,
                    symbol=order_book.symbol,
                    side="short",
                    confidence=min(0.96, 0.66 + abs(rolling) * 0.55 + refill_score * 0.025),
                    trigger_price=mid,
                    source=order_book.source,
                    thesis=(
                        "ask-side absorption keeps refilling; buyers are chewing "
                        "steel and running out of teeth"
                    ),
                    features={"rolling_imbalance": rolling, "refill_score": refill_score},
                )
            )

    def _track_refills(self, order_book: OrderBookSnapshot) -> int:
        score = 0
        visible_levels = order_book.bids[:10] + order_book.asks[:10]
        for level in visible_levels:
            prev_size = self.last_sizes.get(level.price)
            if prev_size is not None and level.size >= prev_size * 1.2:
                self.level_refills[level.price] += 1
            self.last_sizes[level.price] = level.size
            score = max(score, self.level_refills[level.price])
        return score

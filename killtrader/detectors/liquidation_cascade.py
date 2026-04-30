from __future__ import annotations

from collections import deque

from killtrader.config import Settings
from killtrader.core.bus import BinanceCoinMForceOrderEvent, DetectorEvent, EventBus
from killtrader.detectors.base import Detector


class LiquidationCascadeDetector(Detector):
    name = "LiquidationCascadeDetector"

    def __init__(self, settings: Settings, bus: EventBus) -> None:
        super().__init__(settings, bus)
        self.window_ms = settings.liquidation_cascade_window_sec * 1000
        self.events: deque[BinanceCoinMForceOrderEvent] = deque()

    def reset(self) -> None:
        self.events.clear()

    async def on_force_order(self, event: BinanceCoinMForceOrderEvent) -> None:
        self.events.append(event)
        cutoff = event.event_time_ms - self.window_ms
        while self.events and self.events[0].event_time_ms < cutoff:
            self.events.popleft()
        if not self.events:
            return

        total_notional = sum(item.notional_usd for item in self.events)
        sell_notional = sum(item.notional_usd for item in self.events if item.side == "SELL")
        buy_notional = total_notional - sell_notional
        dominant_side = "SELL" if sell_notional >= buy_notional else "BUY"
        dominant_notional = max(sell_notional, buy_notional)
        directional_bias = dominant_notional / total_notional if total_notional else 0.0

        count_triggered = len(self.events) >= self.settings.liquidation_cascade_count_threshold
        notional_triggered = total_notional >= self.settings.liquidation_cascade_usd_threshold
        if not (count_triggered or notional_triggered):
            return
        if directional_bias < self.settings.liquidation_cascade_directional_bias:
            return

        side = "long" if dominant_side == "SELL" else "short"
        price = event.avg_price if event.avg_price > 0 else event.price
        notional_factor = min(
            0.20,
            total_notional / self.settings.liquidation_cascade_usd_threshold * 0.10,
        )
        bias_factor = min(
            0.15,
            directional_bias - self.settings.liquidation_cascade_directional_bias,
        )
        confidence = min(0.98, 0.74 + notional_factor + bias_factor)
        prices = [
            item.avg_price if item.avg_price > 0 else item.price for item in list(self.events)[-10:]
        ]

        await self.bus.publish_detector_event(
            DetectorEvent(
                detector=self.name,
                symbol=event.symbol,
                side=side,
                confidence=confidence,
                trigger_price=price,
                source=event.source,
                thesis=(
                    "forced-flow cascade detected on Binance COIN-M; "
                    "fade the crowded liquidation impulse"
                ),
                features={
                    "liquidation_count": len(self.events),
                    "total_notional_usd": total_notional,
                    "dominant_force_order_side": dominant_side,
                    "directional_bias": directional_bias,
                    "recent_liquidation_prices": prices,
                    "coinm_contract_value_usd": self.settings.binance_coinm_contract_value_usd,
                },
            )
        )

from __future__ import annotations

from collections import deque

from killtrader.config import Settings
from killtrader.core.bus import Candle, DetectorEvent, EventBus
from killtrader.detectors.base import Detector


class LiquidityGrabDetector(Detector):
    name = "LiquidityGrabDetector"

    def __init__(self, settings: Settings, bus: EventBus, wick_excursion_pct: float = 0.08) -> None:
        super().__init__(settings, bus)
        self.wick_excursion_pct = wick_excursion_pct
        self.candles: deque[Candle] = deque(maxlen=settings.liquidity_grab_lookback_bars + 5)

    def reset(self) -> None:
        self.candles.clear()

    async def on_candle(self, candle: Candle) -> None:
        if len(self.candles) < self.settings.liquidity_grab_lookback_bars:
            self.candles.append(candle)
            return

        recent = list(self.candles)[-self.settings.liquidity_grab_lookback_bars :]
        structure_high = max(c.high for c in recent)
        structure_low = min(c.low for c in recent)
        self.candles.append(candle)

        high_excursion = (
            (candle.high - structure_high) / structure_high * 100 if structure_high else 0
        )
        low_excursion = (structure_low - candle.low) / structure_low * 100 if structure_low else 0

        if high_excursion >= self.wick_excursion_pct and candle.close < structure_high:
            confidence = min(
                0.99, 0.68 + high_excursion / 2 + self._reversal_strength(candle, "short")
            )
            await self.bus.publish_detector_event(
                DetectorEvent(
                    detector=self.name,
                    symbol=candle.symbol,
                    side="short",
                    confidence=confidence,
                    trigger_price=candle.close,
                    source=candle.source,
                    thesis=(
                        "buy-side liquidity got raided above structure, then price "
                        "snapped back inside the cage"
                    ),
                    features={
                        "structure_high": structure_high,
                        "wick_excursion_pct": high_excursion,
                    },
                )
            )
        elif low_excursion >= self.wick_excursion_pct and candle.close > structure_low:
            confidence = min(
                0.99, 0.68 + low_excursion / 2 + self._reversal_strength(candle, "long")
            )
            await self.bus.publish_detector_event(
                DetectorEvent(
                    detector=self.name,
                    symbol=candle.symbol,
                    side="long",
                    confidence=confidence,
                    trigger_price=candle.close,
                    source=candle.source,
                    thesis=(
                        "sell-side liquidity got harvested below structure, then the "
                        "trap door slammed shut"
                    ),
                    features={"structure_low": structure_low, "wick_excursion_pct": low_excursion},
                )
            )

    @staticmethod
    def _reversal_strength(candle: Candle, side: str) -> float:
        span = candle.high - candle.low
        if span <= 0:
            return 0.0
        if side == "short":
            return (candle.high - candle.close) / span * 0.18
        return (candle.close - candle.low) / span * 0.18

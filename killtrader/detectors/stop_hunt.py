from __future__ import annotations

from collections import deque
from statistics import mean

from killtrader.config import Settings
from killtrader.core.bus import Candle, DetectorEvent, EventBus
from killtrader.detectors.base import Detector


class StopHuntDetector(Detector):
    name = "StopHuntDetector"

    def __init__(self, settings: Settings, bus: EventBus, cluster_tolerance_pct: float = 0.05) -> None:
        super().__init__(settings, bus)
        self.cluster_tolerance_pct = cluster_tolerance_pct
        self.candles: deque[Candle] = deque(maxlen=160)

    def reset(self) -> None:
        self.candles.clear()

    async def on_candle(self, candle: Candle) -> None:
        self.candles.append(candle)
        if len(self.candles) < 40:
            return
        pivots = self._pivot_clusters(list(self.candles)[:-1])
        avg_volume = mean(c.volume for c in list(self.candles)[-30:-1] if c.volume >= 0)
        volume_surge = candle.volume > max(avg_volume * 1.7, avg_volume + 1e-12)
        if not volume_surge:
            return
        for level in pivots:
            swept_high = candle.high > level and candle.close < level
            swept_low = candle.low < level and candle.close > level
            distance_pct = abs(candle.close - level) / level * 100 if level else 0
            if swept_high:
                await self.bus.publish_detector_event(
                    DetectorEvent(
                        detector=self.name,
                        symbol=candle.symbol,
                        side="short",
                        confidence=min(0.97, 0.72 + distance_pct + 0.1),
                        trigger_price=candle.close,
                        source=candle.source,
                        thesis="engineered stop run through resistance got rejected; late longs are the exit liquidity",
                        features={"swept_level": level, "volume": candle.volume, "avg_volume": avg_volume},
                    )
                )
                return
            if swept_low:
                await self.bus.publish_detector_event(
                    DetectorEvent(
                        detector=self.name,
                        symbol=candle.symbol,
                        side="long",
                        confidence=min(0.97, 0.72 + distance_pct + 0.1),
                        trigger_price=candle.close,
                        source=candle.source,
                        thesis="engineered stop run through support got reclaimed; trapped shorts are fuel",
                        features={"swept_level": level, "volume": candle.volume, "avg_volume": avg_volume},
                    )
                )
                return

    def _pivot_clusters(self, candles: list[Candle]) -> list[float]:
        raw_levels: list[float] = []
        for prev_candle, candle, next_candle in zip(candles, candles[1:], candles[2:]):
            if candle.high > prev_candle.high and candle.high > next_candle.high:
                raw_levels.append(candle.high)
            if candle.low < prev_candle.low and candle.low < next_candle.low:
                raw_levels.append(candle.low)
        clusters: list[list[float]] = []
        for level in sorted(raw_levels):
            if not clusters or abs(level - mean(clusters[-1])) / level * 100 > self.cluster_tolerance_pct:
                clusters.append([level])
            else:
                clusters[-1].append(level)
        return [mean(cluster) for cluster in clusters if len(cluster) >= 2]

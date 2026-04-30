from __future__ import annotations

import asyncio
import json
import traceback

from killtrader.config import load_settings
from killtrader.core.bus import EventBus
from killtrader.detectors.liquidity_grab import LiquidityGrabDetector
from killtrader.detectors.order_book_imbalance import OrderBookImbalanceDetector
from killtrader.detectors.stop_hunt import StopHuntDetector
from killtrader.exchange.blofin import BloFinMarketFeed
from killtrader.feeds.binance_ccxt import BinancePerpFeed


async def fetch_real_market_slice(settings):
    errors: list[str] = []
    blofin = BloFinMarketFeed(settings)
    try:
        candles = await blofin.fetch_candles_once(limit=120)
        book = await blofin.fetch_order_book_once()
        return "blofin", candles, book, errors
    except Exception as exc:
        errors.append(f"blofin: {exc!r}")
    finally:
        await blofin.close()

    binance = BinancePerpFeed(settings)
    try:
        candles = await binance.fetch_candles_once(limit=120)
        book = await binance.fetch_order_book_once()
        return "binance", candles, book, errors
    except Exception as exc:
        errors.append(f"binance: {exc!r}")
        raise RuntimeError("no reachable real candle+book source for detector wiring") from exc
    finally:
        await binance.close()


async def main() -> None:
    settings = load_settings()
    bus = EventBus()
    detectors = [
        LiquidityGrabDetector(settings, bus),
        StopHuntDetector(settings, bus),
        OrderBookImbalanceDetector(settings, bus),
    ]
    try:
        source, candles, book, source_errors = await fetch_real_market_slice(settings)
        for candle in candles:
            for detector in detectors:
                await detector.on_candle(candle)
        for detector in detectors:
            await detector.on_order_book(book)
        events = []
        while not bus.detector_events.empty():
            event = await bus.detector_events.get()
            events.append(
                {
                    "detector": event.detector,
                    "side": event.side,
                    "confidence": event.confidence,
                    "trigger_price": event.trigger_price,
                    "source": event.source,
                }
            )
        result = {
            "ok": True,
            "source": source,
            "candles_processed": len(candles),
            "order_books_processed": 1,
            "events_emitted": len(events),
            "events": events,
            "source_errors_before_success": source_errors,
        }
    except Exception as exc:
        result = {"ok": False, "error": repr(exc), "traceback": traceback.format_exc()}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

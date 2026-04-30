from __future__ import annotations

import asyncio
import json
import time

from killtrader.config import load_settings
from killtrader.core.bus import EventBus
from killtrader.feeds.binance_coinm import BinanceCoinMFeed


async def main() -> None:
    settings = load_settings()
    bus = EventBus()
    feed = BinanceCoinMFeed(settings, bus)
    task = asyncio.create_task(feed.run())
    started = time.time()
    try:
        event = await asyncio.wait_for(bus.coinm_agg_trades.get(), timeout=15)
        result = {
            "ok": True,
            "source": event.source,
            "symbol": event.symbol,
            "price": event.price,
            "quantity": event.quantity,
            "force_orders_seen": bus.coinm_force_orders.qsize(),
            "elapsed_sec": round(time.time() - started, 3),
        }
    except Exception as exc:
        result = {"ok": False, "error": repr(exc), "elapsed_sec": round(time.time() - started, 3)}
    finally:
        task.cancel()
        await feed.close()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

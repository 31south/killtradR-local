from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import asdict

from killtrader.config import load_settings
from killtrader.feeds.binance_ccxt import BinancePerpFeed


async def main() -> None:
    settings = load_settings()
    feed = BinancePerpFeed(settings)
    try:
        candles = await feed.fetch_candles_once(limit=10)
        book = await feed.fetch_order_book_once()
        result = {
            "ok": True,
            "source": "binanceusdm",
            "symbol": settings.binance_symbol,
            "candles": len(candles),
            "last_close": candles[-1].close,
            "best_bid": book.bids[0].price,
            "best_ask": book.asks[0].price,
            "last_candle": asdict(candles[-1]),
        }
    except Exception as exc:
        result = {
            "ok": False,
            "source": "binanceusdm",
            "symbol": settings.binance_symbol,
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        await feed.close()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

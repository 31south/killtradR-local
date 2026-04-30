from __future__ import annotations

import asyncio
import json
import traceback

import websockets

from killtrader.config import load_settings


async def main() -> None:
    settings = load_settings()
    try:
        async with websockets.connect(settings.aggr_trade_ws_url, ping_interval=20) as websocket:
            await websocket.send(
                json.dumps({"op": "subscribe", "channel": "orderbook", "symbol": settings.symbol})
            )
            first = await asyncio.wait_for(websocket.recv(), timeout=10)
            result = {"ok": True, "url": settings.aggr_trade_ws_url, "first_message": first[:1000]}
    except Exception as exc:
        result = {
            "ok": False,
            "url": settings.aggr_trade_ws_url,
            "error": repr(exc),
            "traceback": traceback.format_exc(),
        }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

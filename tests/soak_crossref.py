from __future__ import annotations

import asyncio
import json
import traceback

from killtrader.config import load_settings
from killtrader.core.bus import EventBus
from killtrader.feeds.crossref import CrossReferenceCoordinator


async def main() -> None:
    settings = load_settings()
    settings.symbol = "NOT-A-REAL-MARKET"
    bus = EventBus()
    crossref = CrossReferenceCoordinator(settings, bus)
    try:
        snapshot = await crossref.next_snapshot()
        result = {
            "ok": True,
            "state_after_bad_primary_symbol": crossref.state.value,
            "snapshot_source": snapshot.source,
            "snapshot_symbol": snapshot.symbol,
            "best_bid": snapshot.bids[0].price,
            "best_ask": snapshot.asks[0].price,
            "choke_alerts_queued": bus.choke_alerts.qsize(),
        }
    except Exception as exc:
        result = {"ok": False, "state_after_bad_primary_symbol": crossref.state.value, "error": repr(exc), "traceback": traceback.format_exc(), "choke_alerts_queued": bus.choke_alerts.qsize()}
    finally:
        await crossref.blofin_market.close()
        await crossref.binance.close()
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())

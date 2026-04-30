from __future__ import annotations

import asyncio
import json
from pathlib import Path

from killtrader.journal.query import parse_failure_rate, recent_triggers, win_rate_by_detector
from killtrader.journal.schema import DecisionRow, OutcomeRow, TriggerRow, compact_json
from killtrader.journal.store import JournalStore


def _real_market_snapshot() -> dict:
    depth = json.loads(Path("tests/fixtures/kraken_xbtusdt_depth_real_2026_04_29.json").read_text())
    candles = json.loads(
        Path("tests/fixtures/kraken_xbtusdt_ohlc_real_2026_04_29.json").read_text()
    )["candles"]
    best_bid = float(depth["bids"][0][0])
    best_ask = float(depth["asks"][0][0])
    return {
        "last_price": float(candles[-1][4]),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "recent_ohlcv": candles[-5:],
        "imbalance": (
            sum(float(level[1]) for level in depth["bids"])
            - sum(float(level[1]) for level in depth["asks"])
        )
        / (
            sum(float(level[1]) for level in depth["bids"])
            + sum(float(level[1]) for level in depth["asks"])
        ),
    }


def test_journal_store_and_queries_use_recorded_exchange_values(tmp_path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "journal.db"
        store = JournalStore(str(db_path))
        await store.init()
        snapshot = _real_market_snapshot()
        first = snapshot["recent_ohlcv"][0]
        last = snapshot["recent_ohlcv"][-1]
        confidence = min(0.99, abs(snapshot["imbalance"]) + 0.5)
        trigger = TriggerRow(
            ts_ms=int(first[0]) * 1000,
            symbol="XBTUSDT",
            detector="liquidity_grab",
            confidence=confidence,
            invoked_llm=True,
            feed_source="kraken",
            market_snapshot_json=compact_json(snapshot),
            detector_meta_json=compact_json(
                {"source": "Kraken public APIs", "captured_rows": len(snapshot["recent_ohlcv"])}
            ),
        )
        entry = float(first[4])
        decision = DecisionRow(
            trigger_id=trigger.id,
            ts_ms=int(first[0]) * 1000,
            model="deepseek-coder",
            action="long",
            confidence=confidence,
            entry=entry,
            stop=float(first[3]),
            tp1=float(first[2]),
            tp2=max(float(row[2]) for row in snapshot["recent_ohlcv"]),
            size_pct=float(first[6]),
            reasoning="recorded exchange path held above the sweep low",
            market_maker_thesis="late sellers paid for the reclaim",
            latency_ms=int(last[0]) - int(first[0]),
            parse_ok=True,
            raw_response='{"action":"long"}',
        )
        exit_fill = float(last[4])
        pnl_pct = (exit_fill - entry) / entry * 100
        outcome = OutcomeRow(
            decision_id=decision.id,
            opened_ts_ms=trigger.ts_ms,
            closed_ts_ms=int(last[0]) * 1000,
            entry_fill=entry,
            exit_fill=exit_fill,
            pnl_quote=exit_fill - entry,
            pnl_pct=pnl_pct,
            exit_reason="paper",
            max_favorable_excursion=max(0.0, pnl_pct),
            max_adverse_excursion=min(0.0, pnl_pct),
            is_paper=True,
        )
        await store.insert_many([trigger, decision, outcome])

        recent = await recent_triggers(limit=10, path=str(db_path))
        rates = await win_rate_by_detector(path=str(db_path))
        failures = await parse_failure_rate(path=str(db_path))

        assert recent[0]["detector"] == "liquidity_grab"
        assert rates[0]["sample_count"] == 1
        assert failures[0]["failed"] == 0

    asyncio.run(scenario())

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from killtrader.journal.schema import DecisionRow, OutcomeRow, TriggerRow


class JournalStore:
    def __init__(self, path: str) -> None:
        self.path = str(Path(path))

    async def init(self) -> None:
        parent = Path(self.path).expanduser().parent
        if parent != Path(""):
            parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS triggers (
                    id TEXT PRIMARY KEY,
                    ts_ms INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    detector TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    invoked_llm INTEGER NOT NULL,
                    feed_source TEXT NOT NULL,
                    market_snapshot_json TEXT NOT NULL,
                    detector_meta_json TEXT
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    trigger_id TEXT NOT NULL REFERENCES triggers(id),
                    ts_ms INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    entry REAL,
                    stop REAL,
                    tp1 REAL,
                    tp2 REAL,
                    size_pct REAL,
                    reasoning TEXT,
                    market_maker_thesis TEXT,
                    latency_ms INTEGER NOT NULL,
                    parse_ok INTEGER NOT NULL,
                    raw_response TEXT
                );

                CREATE TABLE IF NOT EXISTS outcomes (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL REFERENCES decisions(id),
                    opened_ts_ms INTEGER,
                    closed_ts_ms INTEGER NOT NULL,
                    entry_fill REAL,
                    exit_fill REAL,
                    pnl_quote REAL,
                    pnl_pct REAL,
                    exit_reason TEXT NOT NULL,
                    max_favorable_excursion REAL,
                    max_adverse_excursion REAL,
                    is_paper INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_triggers_detector_ts ON triggers(detector, ts_ms);
                CREATE INDEX IF NOT EXISTS idx_decisions_trigger ON decisions(trigger_id);
                CREATE INDEX IF NOT EXISTS idx_outcomes_decision ON outcomes(decision_id);
                """
            )
            await db.commit()

    async def insert_many(self, rows: list[TriggerRow | DecisionRow | OutcomeRow]) -> None:
        if not rows:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            for row in rows:
                if isinstance(row, TriggerRow):
                    await self._insert_trigger(db, row)
                elif isinstance(row, DecisionRow):
                    await self._insert_decision(db, row)
                else:
                    await self._insert_outcome(db, row)
            await db.commit()

    @staticmethod
    async def _insert_trigger(db: aiosqlite.Connection, row: TriggerRow) -> None:
        await db.execute(
            """
            INSERT OR REPLACE INTO triggers
            (id, ts_ms, symbol, detector, confidence, invoked_llm, feed_source, market_snapshot_json, detector_meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row.id, row.ts_ms, row.symbol, row.detector, row.confidence, int(row.invoked_llm), row.feed_source, row.market_snapshot_json, row.detector_meta_json),
        )

    @staticmethod
    async def _insert_decision(db: aiosqlite.Connection, row: DecisionRow) -> None:
        await db.execute(
            """
            INSERT OR REPLACE INTO decisions
            (id, trigger_id, ts_ms, model, action, confidence, entry, stop, tp1, tp2, size_pct,
             reasoning, market_maker_thesis, latency_ms, parse_ok, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id,
                row.trigger_id,
                row.ts_ms,
                row.model,
                row.action,
                row.confidence,
                row.entry,
                row.stop,
                row.tp1,
                row.tp2,
                row.size_pct,
                row.reasoning,
                row.market_maker_thesis,
                row.latency_ms,
                int(row.parse_ok),
                row.raw_response,
            ),
        )

    @staticmethod
    async def _insert_outcome(db: aiosqlite.Connection, row: OutcomeRow) -> None:
        await db.execute(
            """
            INSERT OR REPLACE INTO outcomes
            (id, decision_id, opened_ts_ms, closed_ts_ms, entry_fill, exit_fill, pnl_quote, pnl_pct,
             exit_reason, max_favorable_excursion, max_adverse_excursion, is_paper)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id,
                row.decision_id,
                row.opened_ts_ms,
                row.closed_ts_ms,
                row.entry_fill,
                row.exit_fill,
                row.pnl_quote,
                row.pnl_pct,
                row.exit_reason,
                row.max_favorable_excursion,
                row.max_adverse_excursion,
                int(row.is_paper),
            ),
        )

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from killtrader.journal.schema import DecisionRow, OutcomeRow, TriggerRow
from killtrader.journal.store import JournalStore

JournalRow = TriggerRow | DecisionRow | OutcomeRow


@dataclass(slots=True)
class JournalSessionStats:
    triggers: int = 0
    decisions: int = 0
    passes: int = 0
    paper_pnl_quote: float = 0.0
    parse_failures: int = 0


class JournalWriter:
    def __init__(self, path: str, flush_every_n: int = 50, flush_every_sec: float = 5.0, enabled: bool = True) -> None:
        self.enabled = enabled
        self.store = JournalStore(path)
        self.flush_every_n = flush_every_n
        self.flush_every_sec = flush_every_sec
        self.queue: asyncio.Queue[JournalRow] = asyncio.Queue()
        self.stats = JournalSessionStats()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        await self.store.init()
        self._task = asyncio.create_task(self._run(), name="journal-writer")

    def enqueue(self, row: JournalRow) -> None:
        if not self.enabled or self._closed:
            return
        self._record_stat(row)
        self.queue.put_nowait(row)

    async def stop(self) -> None:
        self._closed = True
        if self._task is None:
            return
        await self.queue.join()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        batch: list[JournalRow] = []
        while True:
            try:
                row = await asyncio.wait_for(self.queue.get(), timeout=self.flush_every_sec)
                batch.append(row)
                self.queue.task_done()
                if len(batch) >= self.flush_every_n:
                    await self._flush(batch)
                    batch.clear()
            except asyncio.TimeoutError:
                if batch:
                    await self._flush(batch)
                    batch.clear()
            except asyncio.CancelledError:
                if batch:
                    await self._flush(batch)
                raise

    async def _flush(self, rows: list[JournalRow]) -> None:
        await self.store.insert_many(rows)

    def _record_stat(self, row: JournalRow) -> None:
        if isinstance(row, TriggerRow):
            self.stats.triggers += 1
        elif isinstance(row, DecisionRow):
            if row.action == "pass":
                self.stats.passes += 1
            else:
                self.stats.decisions += 1
            if not row.parse_ok:
                self.stats.parse_failures += 1
        elif isinstance(row, OutcomeRow) and row.is_paper and row.pnl_quote is not None:
            self.stats.paper_pnl_quote += row.pnl_quote


def normalize_detector_name(name: str) -> Literal["liquidity_grab", "stop_hunt", "order_book_imbalance"]:
    mapping = {
        "LiquidityGrabDetector": "liquidity_grab",
        "StopHuntDetector": "stop_hunt",
        "OrderBookImbalanceDetector": "order_book_imbalance",
        "liquidity_grab": "liquidity_grab",
        "stop_hunt": "stop_hunt",
        "order_book_imbalance": "order_book_imbalance",
    }
    return mapping.get(name, name)  # type: ignore[return-value]

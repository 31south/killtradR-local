from __future__ import annotations

from dataclasses import dataclass

from killtrader.core.bus import OrderBookSnapshot
from killtrader.journal.schema import OutcomeRow, now_ms
from killtrader.journal.writer import JournalWriter
from killtrader.signal.schema import TradeSignal


@dataclass(slots=True)
class PaperPosition:
    decision_id: str
    signal: TradeSignal
    opened_ts_ms: int
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0


class PaperOutcomeTracker:
    def __init__(self, writer: JournalWriter, timeout_sec: int) -> None:
        self.writer = writer
        self.timeout_ms = timeout_sec * 1000
        self.positions: dict[str, PaperPosition] = {}

    def track(self, decision_id: str | None, signal: TradeSignal) -> None:
        if decision_id is None or signal.action == "pass":
            return
        self.positions[decision_id] = PaperPosition(
            decision_id=decision_id, signal=signal, opened_ts_ms=now_ms()
        )

    def on_order_book(self, snapshot: OrderBookSnapshot) -> None:
        if not snapshot.bids or not snapshot.asks:
            return
        mid = (snapshot.bids[0].price + snapshot.asks[0].price) / 2
        closed: list[str] = []
        current_ms = snapshot.timestamp_ms or now_ms()
        for decision_id, position in self.positions.items():
            exit_reason = self._exit_reason(
                position.signal, mid, current_ms - position.opened_ts_ms
            )
            pnl_pct = self._pnl_pct(position.signal, mid)
            position.max_favorable_excursion = max(position.max_favorable_excursion, pnl_pct)
            position.max_adverse_excursion = min(position.max_adverse_excursion, pnl_pct)
            if exit_reason is None:
                continue
            self.writer.enqueue(
                OutcomeRow(
                    decision_id=decision_id,
                    opened_ts_ms=position.opened_ts_ms,
                    closed_ts_ms=current_ms,
                    entry_fill=position.signal.entry,
                    exit_fill=mid,
                    pnl_quote=self._pnl_quote(position.signal, mid),
                    pnl_pct=pnl_pct,
                    exit_reason=exit_reason,
                    max_favorable_excursion=position.max_favorable_excursion,
                    max_adverse_excursion=position.max_adverse_excursion,
                    is_paper=True,
                )
            )
            closed.append(decision_id)
        for decision_id in closed:
            self.positions.pop(decision_id, None)

    def _exit_reason(self, signal: TradeSignal, price: float, age_ms: int) -> str | None:
        if age_ms >= self.timeout_ms:
            return "paper"
        if signal.action == "long":
            if price <= signal.stop:
                return "stop"
            if price >= signal.tp2:
                return "tp2"
            if price >= signal.tp1:
                return "tp1"
        if signal.action == "short":
            if price >= signal.stop:
                return "stop"
            if price <= signal.tp2:
                return "tp2"
            if price <= signal.tp1:
                return "tp1"
        return None

    @staticmethod
    def _pnl_pct(signal: TradeSignal, price: float) -> float:
        if signal.entry <= 0 or signal.action == "pass":
            return 0.0
        if signal.action == "long":
            return (price - signal.entry) / signal.entry * 100
        return (signal.entry - price) / signal.entry * 100

    @staticmethod
    def _pnl_quote(signal: TradeSignal, price: float) -> float:
        if signal.action == "long":
            return price - signal.entry
        if signal.action == "short":
            return signal.entry - price
        return 0.0

from __future__ import annotations

from collections import deque

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from killtrader.feeds.crossref import FeedState
from killtrader.journal.writer import JournalSessionStats
from killtrader.signal.schema import TradeSignal


class Dashboard:
    def __init__(self, verbose: bool = False) -> None:
        self.verbose = verbose
        self.signals: deque[TradeSignal] = deque(maxlen=20)
        self.choke_alerts: deque[str] = deque(maxlen=12)
        self.feed_state: FeedState = FeedState.HALTED
        self.imbalance_delta = 0.0
        self.position_lines: list[str] = []
        self.journal_stats = JournalSessionStats()

    def add_signal(self, signal: TradeSignal) -> None:
        self.signals.appendleft(signal)

    def add_choke_alert(self, text: str) -> None:
        self.choke_alerts.appendleft(text)

    def render(self) -> Group:
        return Group(
            self._signal_panel(),
            self._imbalance_panel(),
            self._positions_panel(),
            self._journal_panel(),
            self._choke_panel(),
            Panel(str(self.feed_state.value), title="Active Feed Source", border_style="cyan"),
        )

    def live(self) -> Live:
        return Live(self.render(), refresh_per_second=4, screen=False)

    def _signal_panel(self) -> Panel:
        table = Table(expand=True)
        table.add_column("Action")
        table.add_column("Conf")
        table.add_column("Entry")
        table.add_column("Stop")
        table.add_column("Targets")
        if self.verbose:
            table.add_column("Thesis")
        for signal in self.signals:
            row = [
                signal.action.upper(),
                f"{signal.confidence:.2f}",
                f"{signal.entry:.2f}",
                f"{signal.stop:.2f}",
                f"{signal.tp1:.2f} / {signal.tp2:.2f}",
            ]
            if self.verbose:
                row.append(signal.market_maker_thesis)
            table.add_row(*row)
        return Panel(table, title="Live Signal Feed", border_style="red")

    def _imbalance_panel(self) -> Panel:
        width = 40
        filled = int(min(width, abs(self.imbalance_delta) * width))
        bar = "█" * filled + "░" * (width - filled)
        side = "BID" if self.imbalance_delta >= 0 else "ASK"
        return Panel(
            Text(f"{side} {bar} {self.imbalance_delta:+.2f}"),
            title="Order Book Imbalance",
            border_style="magenta",
        )

    def _positions_panel(self) -> Panel:
        body = (
            "\n".join(self.position_lines)
            if self.position_lines
            else "No active positions tracked."
        )
        return Panel(body, title="Position State + Unrealized P&L", border_style="green")

    def _choke_panel(self) -> Panel:
        body = "\n".join(self.choke_alerts) if self.choke_alerts else "No choke alerts."
        return Panel(body, title="Choke Alert Log", border_style="yellow")

    def _journal_panel(self) -> Panel:
        stats = self.journal_stats
        body = (
            f"Triggers this session: {stats.triggers}\n"
            f"Decisions long/short: {stats.decisions}\n"
            f"Passes: {stats.passes}\n"
            f"Paper P&L quote: {stats.paper_pnl_quote:.4f}\n"
            f"Parse failures: {stats.parse_failures}"
        )
        return Panel(body, title="Journal Stats", border_style="blue")

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from killtrader.config import load_settings
from killtrader.core.bus import EventBus
from killtrader.core.errors import DemoNotSupportedError
from killtrader.core.logger import configure_logging, get_logger
from killtrader.detectors import (
    LiquidationCascadeDetector,
    LiquidityGrabDetector,
    OrderBookImbalanceDetector,
    StopHuntDetector,
)
from killtrader.exchange.blofin import validate_demo_mode_supported
from killtrader.execution.risk import RiskManager
from killtrader.feeds.crossref import CrossReferenceCoordinator
from killtrader.journal.paper_tracker import PaperOutcomeTracker
from killtrader.journal.query import parse_failure_rate, recent_triggers, win_rate_by_detector
from killtrader.journal.writer import JournalWriter
from killtrader.signal.llm import OllamaSignalEngine
from killtrader.ui.dashboard import Dashboard

app = typer.Typer(help="killtradR-local: local Ollama counter-trader for BloFin perps")
journal_app = typer.Typer(help="Inspect the local signal journal")
app.add_typer(journal_app, name="journal")
log = get_logger(__name__)
console = Console()


class SupervisedRunHalt(Exception):
    def __init__(
        self, symbol: str, feed_state: str, alerts: Sequence[str], cause: BaseException
    ) -> None:
        self.symbol = symbol
        self.feed_state = feed_state
        self.alerts = list(alerts)
        self.cause = cause
        super().__init__(f"killtradR halted for {symbol}: {cause}")


async def _run_supervised_tasks(*task_factories: Callable[[], Awaitable[None]]) -> None:
    async with asyncio.TaskGroup() as task_group:
        for task_factory in task_factories:
            task_group.create_task(task_factory())


@app.command()
def version() -> None:
    typer.echo("killtradR-local 0.1.0")


@app.command()
def run(
    symbol: str | None = typer.Option(None, "--symbol"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    settings = load_settings()
    if symbol:
        settings.symbol = symbol
    settings.verbose = verbose or settings.verbose
    configure_logging(settings.log_level)
    try:
        validate_demo_mode_supported(settings)
        asyncio.run(_run(settings))
    except DemoNotSupportedError as exc:
        _print_demo_mode_banner(exc)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print("[yellow]killtradR shutdown requested; exiting cleanly.[/yellow]")
        raise typer.Exit(0) from None
    except asyncio.CancelledError:
        console.print("[yellow]killtradR shutdown requested; exiting cleanly.[/yellow]")
        raise typer.Exit(0) from None
    except SupervisedRunHalt as exc:
        log.exception(
            "killtrader_halted",
            symbol=exc.symbol,
            feed_state=exc.feed_state,
            alerts=exc.alerts,
            error=str(exc.cause),
        )
        _print_halt_banner(exc.symbol, exc.feed_state, exc.cause, exc.alerts)
        raise typer.Exit(1) from exc
    except Exception as exc:
        log.exception(
            "killtrader_halted", symbol=settings.symbol, feed_state="unknown", error=str(exc)
        )
        _print_halt_banner(settings.symbol, "unknown", exc, [])
        raise typer.Exit(1) from exc


async def _run(settings) -> None:
    bus = EventBus()
    dashboard = Dashboard(verbose=settings.verbose)
    journal = JournalWriter(
        settings.journal_path,
        flush_every_n=settings.journal_flush_every_n,
        flush_every_sec=settings.journal_flush_every_sec,
        enabled=settings.journal_enabled,
    )
    await journal.start()
    paper_tracker = PaperOutcomeTracker(journal, settings.paper_position_timeout_sec)
    crossref = CrossReferenceCoordinator(settings, bus)
    detectors = [
        LiquidityGrabDetector(settings, bus),
        StopHuntDetector(settings, bus),
        OrderBookImbalanceDetector(settings, bus),
        LiquidationCascadeDetector(settings, bus),
    ]
    liquidation_detector = next(
        detector for detector in detectors if isinstance(detector, LiquidationCascadeDetector)
    )
    signal_engine = OllamaSignalEngine(settings, journal)
    risk = RiskManager(settings)
    _ = risk

    async def feed_loop() -> None:
        while True:
            snapshot = await crossref.next_snapshot()
            candles = await crossref.next_candles()
            dashboard.feed_state = crossref.state
            bid_volume = sum(level.size for level in snapshot.bids[:20])
            ask_volume = sum(level.size for level in snapshot.asks[:20])
            total = bid_volume + ask_volume
            dashboard.imbalance_delta = (bid_volume - ask_volume) / total if total else 0
            if not settings.trade_enabled:
                paper_tracker.on_order_book(snapshot)
            for detector in detectors:
                await detector.on_order_book(snapshot)
            for candle in candles[-3:]:
                for detector in detectors:
                    await detector.on_candle(candle)
            await asyncio.sleep(1)

    async def signal_loop() -> None:
        while True:
            event = await bus.detector_events.get()
            signal = await signal_engine.decide(event)
            if signal:
                dashboard.add_signal(signal)
                if not settings.trade_enabled:
                    paper_tracker.track(signal.journal_decision_id, signal)

    async def alert_loop() -> None:
        while True:
            alert = await bus.choke_alerts.get()
            dashboard.add_choke_alert(alert.message)

    async def coinm_stream_loop() -> None:
        await crossref.binance_coinm.run()

    async def liquidation_loop() -> None:
        while True:
            event = await bus.coinm_force_orders.get()
            dashboard.add_liquidation(event)
            await liquidation_detector.on_force_order(event)

    async def dashboard_loop() -> None:
        while True:
            dashboard.journal_stats = journal.stats
            live.update(dashboard.render())
            await asyncio.sleep(0.25)

    with dashboard.live() as live:
        try:
            await _run_supervised_tasks(
                feed_loop,
                signal_loop,
                alert_loop,
                coinm_stream_loop,
                liquidation_loop,
                dashboard_loop,
            )
        except BaseExceptionGroup as exc_group:
            terminal_error = _first_terminal_error(exc_group)
            if terminal_error is None:
                raise
            dashboard.feed_state = crossref.state
            dashboard.add_choke_alert(f"HALTED: {terminal_error}")
            live.update(dashboard.render())
            raise SupervisedRunHalt(
                settings.symbol,
                crossref.state.value,
                list(crossref.last_alerts)[-5:],
                terminal_error,
            ) from terminal_error
        finally:
            await crossref.close()
            await journal.stop()


def _print_demo_mode_banner(exc: DemoNotSupportedError) -> None:
    console.print(
        Panel.fit(
            f"{exc}\n\n"
            "Options:\n"
            "  1. Upgrade or downgrade blofin to a version that supports demo mode, "
            "if one exists.\n"
            "  2. Set USE_DEMO=false and TRADE_ENABLED=false for a safe signals-only "
            "run\n"
            "     against real public market data with no order execution.\n\n"
            "Exiting.",
            title="[bold red]BloFin demo mode unavailable[/bold red]",
            border_style="red",
        )
    )


def _first_terminal_error(exc: BaseException) -> BaseException | None:
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            terminal = _first_terminal_error(nested)
            if terminal is not None:
                return terminal
        return None
    if isinstance(exc, asyncio.CancelledError):
        return None
    return exc


def _print_halt_banner(
    symbol: str, feed_state: str, exc: BaseException, alerts: Sequence[str]
) -> None:
    alert_text = (
        "\n".join(f"• {alert}" for alert in alerts[-5:])
        if alerts
        else "No choke alerts recorded before halt."
    )
    console.print(
        Panel.fit(
            f"[bold]Symbol:[/bold] {symbol}\n"
            f"[bold]Feed state:[/bold] {feed_state}\n"
            f"[bold]Error:[/bold] {type(exc).__name__}: {exc}\n\n"
            f"[bold]Recent source alerts:[/bold]\n{alert_text}",
            title="[bold red]killtradR HALTED[/bold red]",
            border_style="red",
        )
    )


@journal_app.command("stats")
def journal_stats() -> None:
    settings = load_settings()
    rows = asyncio.run(win_rate_by_detector(path=settings.journal_path))
    table = Table(title="killtradR Journal Stats")
    table.add_column("Detector")
    table.add_column("Samples", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("Avg PnL %", justify="right")
    table.add_column("Buckets")
    if not rows:
        table.add_row("no rows", "0", "0.00%", "0.0000", "journal empty")
    for row in rows:
        buckets = ", ".join(
            f"{name}:{value['sample_count']}" for name, value in row["confidence_buckets"].items()
        )
        table.add_row(
            row["detector"],
            str(row["sample_count"]),
            f"{row['win_rate'] * 100:.2f}%",
            f"{row['avg_pnl_pct']:.4f}",
            buckets,
        )
    console.print(table)


@journal_app.command("recent")
def journal_recent(
    limit: int = typer.Option(20, "--limit"),
    detector: str | None = typer.Option(None, "--detector"),
) -> None:
    settings = load_settings()
    rows = asyncio.run(recent_triggers(limit=limit, detector=detector, path=settings.journal_path))
    table = Table(title="Recent Journal Rows")
    table.add_column("Time ms")
    table.add_column("Detector")
    table.add_column("Conf", justify="right")
    table.add_column("Source")
    table.add_column("Action")
    table.add_column("Parse")
    if not rows:
        table.add_row("-", "no rows", "0.00", "-", "-", "-")
    for row in rows:
        table.add_row(
            str(row["ts_ms"]),
            row["detector"],
            f"{row['confidence']:.2f}",
            row["feed_source"],
            str(row.get("action") or "-"),
            str(row.get("parse_ok") if row.get("parse_ok") is not None else "-"),
        )
    console.print(table)


@journal_app.command("parse-failures")
def journal_parse_failures() -> None:
    settings = load_settings()
    rows = asyncio.run(parse_failure_rate(path=settings.journal_path))
    table = Table(title="LLM JSON Parse Failures")
    table.add_column("Model")
    table.add_column("Total", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Rate", justify="right")
    if not rows:
        table.add_row("no rows", "0", "0", "0.00%")
    for row in rows:
        table.add_row(
            row["model"], str(row["total"]), str(row["failed"]), f"{row['failure_rate'] * 100:.2f}%"
        )
    console.print(table)


if __name__ == "__main__":
    app()

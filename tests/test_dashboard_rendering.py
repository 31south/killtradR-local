from __future__ import annotations

import io

from rich.console import Console

from killtrader.ui.dashboard import Dashboard


def _render_once(dashboard: Dashboard) -> str:
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, color_system=None, width=120, height=40)
    console.print(dashboard.render())
    return stream.getvalue()


def test_dashboard_layout_contains_one_panel_set() -> None:
    output = _render_once(Dashboard(verbose=False))

    assert output.count("Live Signal Feed") == 1
    assert output.count("Order Book Imbalance") == 1
    assert output.count("Position State + Unrealized P&L") == 1
    assert output.count("Journal Stats") == 1
    assert output.count("Recent Liquidations") == 1
    assert output.count("Choke Alert Log") == 1
    assert output.count("Active Feed Source") == 1


def test_dashboard_live_updates_single_renderable() -> None:
    dashboard = Dashboard(verbose=False)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, color_system=None, width=120, height=40)

    with dashboard.live(console=console) as live:
        for _ in range(3):
            live.update(dashboard.render(), refresh=True)

    final_frame = _render_once(dashboard)
    assert final_frame.count("Live Signal Feed") == 1

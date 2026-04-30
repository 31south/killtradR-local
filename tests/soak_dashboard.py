from __future__ import annotations

import io
import json
import traceback

from rich.console import Console

from killtrader.ui.dashboard import Dashboard


def main() -> None:
    try:
        dashboard = Dashboard(verbose=False)
        sink = io.StringIO()
        console = Console(file=sink, force_terminal=False, width=120)
        console.print(dashboard.render())
        rendered = sink.getvalue()
        result = {"ok": True, "rendered_chars": len(rendered), "contains_signal_panel": "Live Signal Feed" in rendered}
    except Exception as exc:
        result = {"ok": False, "error": repr(exc), "traceback": traceback.format_exc()}
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

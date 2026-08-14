"""`oknoll ui` — the daemon's minimal local web UI (arrives with the daemon)."""

from __future__ import annotations

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, not_yet

ui_app = typer.Typer(help="Open the local daemon's web UI.", no_args_is_help=True)


@ui_app.command("open", context_settings=STUB_CONTEXT)
def open_() -> None:
    """Open the daemon UI in a browser."""
    not_yet("ui open", "the local daemon")

"""`oknoll daemon` — the persistent multi-bundle runtime (arrives with the daemon)."""

from __future__ import annotations

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, not_yet

daemon_app = typer.Typer(
    help="Run the local multi-bundle daemon (UI, HTTP API, MCP endpoint).",
    no_args_is_help=True,
)


@daemon_app.command("start", context_settings=STUB_CONTEXT)
def start() -> None:
    """Start the local daemon."""
    not_yet("daemon start", "the local daemon")


@daemon_app.command("stop", context_settings=STUB_CONTEXT)
def stop() -> None:
    """Stop the local daemon."""
    not_yet("daemon stop", "the local daemon")


@daemon_app.command("status", context_settings=STUB_CONTEXT)
def status() -> None:
    """Report daemon health and served bundles."""
    not_yet("daemon status", "the local daemon")


@daemon_app.command("logs", context_settings=STUB_CONTEXT)
def logs() -> None:
    """Show daemon logs."""
    not_yet("daemon logs", "the local daemon")


@daemon_app.command("install", context_settings=STUB_CONTEXT)
def install() -> None:
    """Install the daemon as an OS service."""
    not_yet("daemon install", "the local daemon")


@daemon_app.command("uninstall", context_settings=STUB_CONTEXT)
def uninstall() -> None:
    """Remove the daemon OS service."""
    not_yet("daemon uninstall", "the local daemon")

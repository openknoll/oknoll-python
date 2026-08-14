"""`oknoll registry` — inspect remote registries (arrives with the registry client)."""

from __future__ import annotations

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, not_yet

registry_app = typer.Typer(
    help="Inspect OCI registries serving OkNoll images.", no_args_is_help=True
)


@registry_app.command("inspect", context_settings=STUB_CONTEXT)
def inspect() -> None:
    """Inspect a remote registry reference without pulling it."""
    not_yet("registry inspect", "the registry client")

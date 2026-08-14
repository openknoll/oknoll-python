"""`oknoll auth` — registry and hosted-service authentication."""

from __future__ import annotations

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, not_yet

auth_app = typer.Typer(
    help="Authenticate against registries and hosted OpenKnoll services.",
    no_args_is_help=True,
)


@auth_app.command("login", context_settings=STUB_CONTEXT)
def login() -> None:
    """Sign in for registry pushes and hosted services."""
    not_yet("auth login", "the hosted control plane")

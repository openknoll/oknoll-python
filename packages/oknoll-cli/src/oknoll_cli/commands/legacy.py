"""Hidden pointer stubs for the flat v0.3 command surface.

The v0.4.0 clean break replaced every flat top-level command with a resource
namespace. Each old name stays registered — hidden — so a v0.3 invocation
breaks loudly with the exact replacement printed, never silently. The stubs
never perform work: any arguments (including --help) are swallowed, one line
is printed to stderr, and the exit code is 2.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer

MOVED_EXIT = 2

# old top-level command -> replacement namespaced command
MOVED: dict[str, str] = {
    "init": "project init",
    "add": "source add",
    "build": "project build",
    "lint": "bundle lint",
    "ask": "query ask",
    "chat": "query chat",
    "serve": "mcp stdio",
    "pack": "bundle pack",
    "diff": "bundle diff",
    "plugin": "system plugins",
    "login": "auth login",
    "keys": "auth login",  # keys dropped; auth login is the whole surface
    "eval": "system eval",
    "viz": "system viz",
    "doctor": "system doctor",
}

_SWALLOW_EVERYTHING: dict[str, Any] = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}


def _make_stub(new: str) -> Callable[[], None]:
    # Zero-parameter on purpose: Typer must see no CLI parameters at all.
    def _stub() -> None:
        typer.secho(f"moved: use 'oknoll {new}'", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=MOVED_EXIT)

    return _stub


def register_legacy_stubs(app: typer.Typer) -> None:
    for old, new in MOVED.items():
        app.command(
            name=old,
            hidden=True,
            add_help_option=False,
            context_settings=_SWALLOW_EVERYTHING,
        )(_make_stub(new))

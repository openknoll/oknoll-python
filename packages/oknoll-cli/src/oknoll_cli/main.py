"""oknoll CLI — the v0.4 resource-namespace surface.

`oknoll <resource> <command> [target] [options]`: the flat v0.3 surface was
replaced wholesale at v0.4.0 by resource namespaces. Old top-level commands
are hidden pointer stubs (see commands/legacy.py) that print their replacement
and exit 2 — they never perform work. Namespaced commands whose capability has
not shipped yet fail with a clear "not yet" error naming the feature area that
delivers them. The namespace tree is frozen by tests: adding or renaming a
command or namespace stays a deliberate, recorded act.
"""

from __future__ import annotations

import typer
from okf_core import __version__ as core_version

from oknoll_cli.commands.auth import auth_app
from oknoll_cli.commands.bundle import bundle_app
from oknoll_cli.commands.config import config_app
from oknoll_cli.commands.daemon import daemon_app
from oknoll_cli.commands.image import image_app
from oknoll_cli.commands.legacy import register_legacy_stubs
from oknoll_cli.commands.mcp import mcp_app
from oknoll_cli.commands.project import project_app
from oknoll_cli.commands.query import query_app
from oknoll_cli.commands.registry import registry_app
from oknoll_cli.commands.source import source_app
from oknoll_cli.commands.system import system_app
from oknoll_cli.commands.ui import ui_app

app = typer.Typer(
    name="oknoll",
    help="Local-first OKF bundle compiler, deterministic explorer, and knowledge runtime.",
    no_args_is_help=True,
    add_completion=False,
)

NAMESPACES: dict[str, typer.Typer] = {
    "project": project_app,
    "source": source_app,
    "bundle": bundle_app,
    "image": image_app,
    "registry": registry_app,
    "auth": auth_app,
    "daemon": daemon_app,
    "query": query_app,
    "mcp": mcp_app,
    "ui": ui_app,
    "config": config_app,
    "system": system_app,
}
for _name, _sub_app in NAMESPACES.items():
    app.add_typer(_sub_app, name=_name)

register_legacy_stubs(app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"oknoll {core_version}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """oknoll — build, lint, explore, pack, and (soon) install and serve OKF bundles."""


if __name__ == "__main__":
    app()

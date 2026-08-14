"""`oknoll mcp` — expose bundles to MCP clients."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, fail, load_project, not_yet

mcp_app = typer.Typer(
    help="Serve bundles to MCP clients (stdio now; daemon endpoint later).",
    no_args_is_help=True,
)


@mcp_app.command("stdio")
def stdio(
    bundle: Path | None = typer.Argument(
        None,
        help="Bundle directory to serve (default: the active project's bundle). "
        "Works on any bundle, including read-only foreign ones.",
    ),
) -> None:
    """Serve one bundle's seven deterministic navigation tools over stdio MCP."""
    from oknoll_cli.mcp_server import run_stdio

    bundle_root = bundle if bundle is not None else load_project().bundle_path
    # stdout is the MCP transport from here on — status goes to stderr only.
    typer.secho(f"serving {bundle_root} over stdio MCP (ctrl-c to stop)", err=True)
    try:
        run_stdio(bundle_root, today=date.today().isoformat())
    except ValueError as exc:
        raise fail(f"oknoll mcp stdio: {exc}") from exc
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None


@mcp_app.command("endpoint", context_settings=STUB_CONTEXT)
def endpoint() -> None:
    """Print the daemon's MCP endpoint."""
    not_yet("mcp endpoint", "the local daemon")


@mcp_app.command("config", context_settings=STUB_CONTEXT)
def config() -> None:
    """Print (or install) client-specific MCP configuration."""
    not_yet("mcp config", "the local daemon")


@mcp_app.command("inspect", context_settings=STUB_CONTEXT)
def inspect() -> None:
    """Show the daemon MCP server's tool surface and served bundles."""
    not_yet("mcp inspect", "the local daemon")

"""`oknoll mcp` — expose bundles to MCP clients."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer

from oknoll_cli.commands.common import (
    STUB_CONTEXT,
    fail,
    load_project,
    not_yet,
    parse_cli_locator,
    resolve_installed,
    runtime_store_catalog,
)

mcp_app = typer.Typer(
    help="Serve bundles to MCP clients (stdio now; daemon endpoint later).",
    no_args_is_help=True,
)


@mcp_app.command("stdio")
def stdio(
    bundle: str | None = typer.Argument(
        None,
        help="Bundle to serve: a directory path, an installed alias, or a "
        "local:<alias> locator (default: the active project's bundle). "
        "Works on any bundle, including read-only foreign ones.",
    ),
) -> None:
    """Serve one bundle's seven deterministic navigation tools over stdio MCP."""
    from oknoll_cli.mcp_server import run_stdio

    bundle_root = _resolve_stdio_target(bundle)
    # stdout is the MCP transport from here on — status goes to stderr only.
    typer.secho(f"serving {bundle_root} over stdio MCP (ctrl-c to stop)", err=True)
    try:
        run_stdio(bundle_root, today=date.today().isoformat())
    except ValueError as exc:
        raise fail(f"oknoll mcp stdio: {exc}") from exc
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None


def _resolve_stdio_target(bundle: str | None) -> Path:
    """Resolve the stdio target: project default, directory path, or installed alias."""
    if bundle is None:
        return load_project().bundle_path
    locator = parse_cli_locator(bundle)
    if locator.kind in ("oci", "oknoll"):
        not_yet("mcp stdio (remote locators)", "the registry client")
    if locator.kind == "path":
        tree = Path(locator.path or "").expanduser()
        if not tree.is_dir():
            raise fail(f"bundle directory not found: {tree}")
        return tree
    from oknoll_runtime import InstallError, StoreError, installed_tree

    store, catalog = runtime_store_catalog()
    entry = resolve_installed(catalog, bundle)
    try:
        tree = installed_tree(store, entry)
    except (InstallError, StoreError) as exc:
        raise fail(f"oknoll mcp stdio: {exc}") from exc
    # Immutable identity for the operator; stdout stays the MCP transport.
    typer.secho(f"{entry.alias}@{entry.oci_digest} ({entry.okf_revision})", err=True)
    catalog.touch_last_used(entry.alias)
    return tree


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

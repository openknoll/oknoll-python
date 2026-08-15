"""`oknoll mcp` — expose bundles to MCP clients."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from oknoll_runtime import runtime_dirs

from oknoll_cli.commands.common import (
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


def _daemon_endpoint() -> tuple[str, bool]:
    """The daemon MCP URL and whether a daemon is currently serving it."""
    from oknoll_runtime.daemon import DEFAULT_PORT, daemon_url, read_record

    record = read_record(runtime_dirs().state)
    if record is None:
        return f"http://127.0.0.1:{DEFAULT_PORT}/mcp", False
    return f"{daemon_url(record)}/mcp", True


@mcp_app.command("endpoint")
def endpoint(
    show_token: bool = typer.Option(
        False, "--show-token", help="Also print the bearer token clients must send."
    ),
) -> None:
    """Print the daemon's MCP endpoint."""
    from oknoll_runtime.daemon import ensure_token

    url, running = _daemon_endpoint()
    if not running:
        typer.secho(
            "the daemon is not running — start it with `oknoll daemon start`",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(url)
    if show_token:
        typer.echo(ensure_token(runtime_dirs().state))


@mcp_app.command("config")
def config(
    client: str = typer.Option(..., "--client", help="Target client: claude or codex."),
    install: bool = typer.Option(
        False,
        "--install",
        help="Write the configuration into the client instead of printing it "
        "(claude only; runs the `claude` CLI).",
    ),
) -> None:
    """Print (or install) client-specific MCP configuration for the daemon."""
    import json
    import shutil
    import subprocess

    from oknoll_runtime.daemon import ensure_token

    if client not in ("claude", "codex"):
        raise fail(f"oknoll mcp config: unknown client {client!r} (claude|codex)")
    url, running = _daemon_endpoint()
    token = ensure_token(runtime_dirs().state)
    if not running:
        typer.secho(
            "note: the daemon is not running — clients connect once you run `oknoll daemon start`",
            fg=typer.colors.YELLOW,
            err=True,
        )

    if client == "claude":
        server = {
            "type": "http",
            "url": url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        if install:
            binary = shutil.which("claude")
            if binary is None:
                raise fail(
                    "oknoll mcp config: `claude` CLI not found — print the config "
                    "instead and add it by hand"
                )
            result = subprocess.run(
                [binary, "mcp", "add-json", "oknoll", json.dumps(server), "--scope", "user"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise fail(f"oknoll mcp config: claude CLI refused the config: {detail}")
            typer.secho(
                "installed MCP server 'oknoll' for Claude (user scope)", fg=typer.colors.GREEN
            )
            return
        typer.echo(json.dumps({"mcpServers": {"oknoll": server}}, indent=2))
        return

    # codex: print a config.toml snippet; codex has no supported non-interactive install.
    if install:
        raise fail(
            "oknoll mcp config: --install is not supported for codex — append the "
            "printed snippet to ~/.codex/config.toml"
        )
    typer.echo("[mcp_servers.oknoll]")
    typer.echo(f'url = "{url}"')
    typer.echo(f'http_headers = {{ Authorization = "Bearer {token}" }}')


@mcp_app.command("inspect")
def inspect() -> None:
    """Show the daemon MCP server's tool surface and served bundles."""
    from oknoll_runtime import TOOL_NAMES

    url, running = _daemon_endpoint()
    state = "running" if running else "not running"
    typer.echo(f"endpoint: {url} ({state})")
    typer.echo("tools:")
    for name in ("bundles_list", "bundles_get", *TOOL_NAMES):
        scope = "" if name.startswith("bundles_") else "  (per-bundle: requires `bundle`)"
        typer.echo(f"  - {name}{scope}")
    _, catalog = runtime_store_catalog()
    entries = catalog.list_entries()
    typer.echo(f"bundles served: {len(entries)}")
    for entry in entries:
        typer.echo(f"  - {entry.alias}@{entry.oci_digest} ({entry.okf_revision})")

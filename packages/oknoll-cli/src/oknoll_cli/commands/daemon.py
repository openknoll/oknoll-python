"""`oknoll daemon` — the persistent multi-bundle runtime process.

One loopback daemon serves every installed bundle over three surfaces of a
single origin: `/` (web UI), `/api/v1` (HTTP/JSON), `/mcp` (MCP Streamable
HTTP). `start` detaches by default and confirms health over HTTP before
returning; `--foreground` serves in this process (that is also what the
detached child runs). OS-service installation is deferred by decision — the
daemon is started per login session for now.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path

import typer
from oknoll_runtime import runtime_dirs
from oknoll_runtime.daemon import (
    DEFAULT_PORT,
    daemon_health,
    daemon_log_path,
    daemon_url,
    read_record,
)

from oknoll_cli.commands.common import STUB_CONTEXT, fail

daemon_app = typer.Typer(
    help="Run the local multi-bundle daemon (UI, HTTP API, MCP endpoint).",
    no_args_is_help=True,
)

_START_TIMEOUT_SECONDS = 15.0
_STOP_TIMEOUT_SECONDS = 10.0


def _deferred(command: str, capability: str) -> None:
    typer.secho(
        f"oknoll {command}: deferred — {capability}.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


def _serve_foreground(port: int) -> None:
    """Build the app with this process's provider config and serve until stopped."""
    from oknoll_providers import ProviderError, load_env
    from oknoll_providers import resolve as resolve_provider_spec
    from oknoll_providers import resolve_embedder as resolve_embedder_spec
    from oknoll_runtime.daemon import build_app, serve_foreground

    from oknoll_cli import global_config

    try:
        load_env(Path.cwd())
        global_config.apply_global_env()
    except (ProviderError, global_config.GlobalConfigError) as exc:
        raise fail(str(exc)) from exc

    def provider_resolver(spec: str | None) -> object:
        return resolve_provider_spec(spec or global_config.load_global_config().model or "stub")

    def embedder_resolver(spec: str | None) -> object:
        return resolve_embedder_spec(spec or global_config.load_global_config().embedder or "stub")

    dirs = runtime_dirs()
    app = build_app(
        dirs,
        provider_resolver=provider_resolver,  # type: ignore[arg-type]
        embedder_resolver=embedder_resolver,  # type: ignore[arg-type]
        today=date.today().isoformat(),
    )
    typer.secho(f"oknoll daemon serving http://127.0.0.1:{port} (ctrl-c to stop)", err=True)
    serve_foreground(app, port=port, state_dir=dirs.state)


@daemon_app.command("start")
def start(
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Loopback port to serve on."),
    foreground: bool = typer.Option(
        False, "--foreground", help="Serve in this process instead of detaching."
    ),
) -> None:
    """Start the local daemon (detached by default; loopback only)."""
    dirs = runtime_dirs()
    record = read_record(dirs.state)
    if record is not None:
        raise fail(
            f"daemon already running (pid {record.pid}, {daemon_url(record)}) — "
            "see `oknoll daemon status`"
        )

    if foreground:
        _serve_foreground(port)
        return

    log_path = daemon_log_path(dirs.state)
    dirs.state.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from oknoll_cli.main import app; app()",
                "daemon",
                "start",
                "--foreground",
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=Path.cwd(),
        )

    deadline = time.monotonic() + _START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        health = daemon_health(dirs.state)
        if health is not None and health.get("port") == port:
            typer.secho(
                f"daemon running (pid {health['pid']}) — http://127.0.0.1:{port}",
                fg=typer.colors.GREEN,
            )
            typer.echo(f"  ui:   http://127.0.0.1:{port}/  (open with `oknoll ui open`)")
            typer.echo(f"  api:  http://127.0.0.1:{port}/api/v1")
            typer.echo(f"  mcp:  http://127.0.0.1:{port}/mcp  (see `oknoll mcp config`)")
            return
        if process.poll() is not None:
            break
        time.sleep(0.2)
    raise fail(
        f"daemon failed to start — see `oknoll daemon logs` (log: {daemon_log_path(dirs.state)})"
    )


@daemon_app.command("stop")
def stop() -> None:
    """Stop the local daemon."""
    dirs = runtime_dirs()
    record = read_record(dirs.state)
    if record is None:
        typer.echo("daemon is not running")
        return
    try:
        os.kill(record.pid, signal.SIGTERM)
    except OSError as exc:
        raise fail(f"could not signal daemon pid {record.pid}: {exc.strerror}") from exc
    deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if read_record(dirs.state) is None:
            typer.secho(f"daemon stopped (pid {record.pid})", fg=typer.colors.GREEN)
            return
        time.sleep(0.2)
    raise fail(f"daemon pid {record.pid} did not exit within {_STOP_TIMEOUT_SECONDS:.0f}s")


@daemon_app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Emit the health report as JSON."),
) -> None:
    """Report daemon health and served bundles."""
    import json as json_mod

    dirs = runtime_dirs()
    health = daemon_health(dirs.state)
    if health is None:
        if json_output:
            typer.echo(json_mod.dumps({"status": "stopped"}))
        else:
            typer.echo("daemon is not running — start it with `oknoll daemon start`")
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json_mod.dumps(health, indent=2, sort_keys=True))
        return
    typer.secho(
        f"daemon running (pid {health['pid']}, since {health['started_at']})",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  endpoint: http://127.0.0.1:{health['port']}")
    typer.echo(f"  version:  {health.get('version', '?')}")
    typer.echo(f"  bundles:  {health.get('bundles', '?')} installed")
    typer.echo(f"  logs:     {daemon_log_path(dirs.state)}")


@daemon_app.command("logs")
def logs(
    tail: int = typer.Option(100, "--tail", help="Number of trailing lines to show."),
) -> None:
    """Show the daemon's log tail."""
    log_path = daemon_log_path(runtime_dirs().state)
    if not log_path.is_file():
        typer.echo("no daemon log yet")
        return
    lines: deque[str] = deque(maxlen=max(1, tail))
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines.append(line.rstrip("\n"))
    for line in lines:
        typer.echo(line)


@daemon_app.command("install", context_settings=STUB_CONTEXT)
def install() -> None:
    """Install the daemon as an OS service."""
    _deferred("daemon install", "OS-service installation; run `oknoll daemon start` per session")


@daemon_app.command("uninstall", context_settings=STUB_CONTEXT)
def uninstall() -> None:
    """Remove the daemon OS service."""
    _deferred("daemon uninstall", "OS-service installation; nothing is installed")

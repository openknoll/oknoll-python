"""Daemon lifecycle at the CLI boundary — the Phase-17 exit gate, locally.

Installs two bundles, starts a real detached daemon on a free loopback port,
and drives it the way a user would: status, mcp endpoint/config, a
multi-bundle chat with qualified identities, then stop. Everything runs
under the conftest's per-test OKNOLL_HOME.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from oknoll_cli.main import app
from oknoll_runtime import runtime_dirs
from oknoll_runtime.daemon import read_record, read_token
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"
FOREIGN = REPO_ROOT / "fixtures" / "bundles" / "upstream" / "acme_retail"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture()
def running_daemon() -> Iterator[int]:
    for source, alias in ((MULTIHOP, "golden"), (FOREIGN, "acme")):
        result = runner.invoke(app, ["bundle", "install", str(source), "--name", alias])
        assert result.exit_code == 0, _output(result)

    port = _free_port()
    started = runner.invoke(app, ["daemon", "start", "--port", str(port)])
    assert started.exit_code == 0, _output(started)
    try:
        yield port
    finally:
        record = read_record(runtime_dirs().state)
        stopped = runner.invoke(app, ["daemon", "stop"])
        if stopped.exit_code != 0 and record is not None:
            with contextlib.suppress(OSError):
                os.kill(record.pid, signal.SIGKILL)


def test_daemon_lifecycle_and_endpoints(running_daemon: int) -> None:
    port = running_daemon

    status = runner.invoke(app, ["daemon", "status", "--json"])
    assert status.exit_code == 0, _output(status)
    health = json.loads(status.output)
    assert health["status"] == "ok"
    assert health["port"] == port
    assert health["bundles"] == 2

    endpoint = runner.invoke(app, ["mcp", "endpoint"])
    assert endpoint.exit_code == 0
    assert f"http://127.0.0.1:{port}/mcp" in endpoint.output

    config = runner.invoke(app, ["mcp", "config", "--client", "claude"])
    assert config.exit_code == 0, _output(config)
    snippet = json.loads(config.output)
    server = snippet["mcpServers"]["oknoll"]
    assert server["url"] == f"http://127.0.0.1:{port}/mcp"
    token = read_token(runtime_dirs().state)
    assert token is not None
    assert server["headers"]["Authorization"] == f"Bearer {token}"

    # The advertised endpoint really serves: authenticated API answers, and
    # the token from `mcp config` is the one the daemon enforces.
    denied = httpx.get(f"http://127.0.0.1:{port}/api/v1/catalog", timeout=5.0)
    assert denied.status_code == 401
    catalog = httpx.get(
        f"http://127.0.0.1:{port}/api/v1/catalog",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5.0,
    )
    assert catalog.status_code == 200
    assert [b["alias"] for b in catalog.json()["bundles"]] == ["acme", "golden"]

    logs = runner.invoke(app, ["daemon", "logs"])
    assert logs.exit_code == 0

    second = runner.invoke(app, ["daemon", "start", "--port", str(port)])
    assert second.exit_code == 1
    assert "already running" in _output(second)


def test_daemon_chat_explores_two_bundles_with_qualified_identity(
    running_daemon: int,
) -> None:
    chat = runner.invoke(
        app,
        ["query", "chat", "--bundle", "golden", "--bundle", "acme"],
        input="What is the incident response process?\n@acme What is the return policy?\nexit\n",
    )
    assert chat.exit_code == 0, _output(chat)
    text = _output(chat)
    assert "golden@sha256:" in text
    assert "acme@sha256:" in text


def test_stop_clears_the_record(running_daemon: int) -> None:
    stopped = runner.invoke(app, ["daemon", "stop"])
    assert stopped.exit_code == 0, _output(stopped)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and read_record(runtime_dirs().state) is not None:
        time.sleep(0.1)
    assert read_record(runtime_dirs().state) is None
    again = runner.invoke(app, ["daemon", "stop"])
    assert again.exit_code == 0
    assert "not running" in _output(again)

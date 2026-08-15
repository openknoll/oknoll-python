"""Daemon lifecycle plumbing: pidfile, run record, health handshake.

The CLI drives start/stop/status; this module owns the state-directory
artifacts they share. The run record (`daemon.json`) is written by the
serving process itself once it owns the port, so a record that exists and
whose pid is alive means "a daemon is (or was moments ago) serving". Health
is always confirmed over HTTP with the bearer token — a pid check alone
cannot tell a wedged process from a serving one.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from oknoll_runtime.daemon.security import read_token

DEFAULT_PORT = 7465
RECORD_FILENAME = "daemon.json"
LOG_FILENAME = "daemon.log"


@dataclass(frozen=True, slots=True)
class DaemonRecord:
    pid: int
    port: int
    started_at: str


def record_path(state_dir: Path) -> Path:
    return state_dir / RECORD_FILENAME


def daemon_log_path(state_dir: Path) -> Path:
    return state_dir / LOG_FILENAME


def daemon_url(record: DaemonRecord) -> str:
    return f"http://127.0.0.1:{record.port}"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # When the daemon is our own child (tests, a wrapping process), it stays a
    # signalable zombie after exit until reaped — reap it so "alive" means
    # "actually running". waitpid on a non-child raises and changes nothing.
    with contextlib.suppress(ChildProcessError, OSError):
        reaped, _status = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def read_record(state_dir: Path, *, prune_stale: bool = True) -> DaemonRecord | None:
    """The current run record, pruning one whose process is gone."""
    path = record_path(state_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        record = DaemonRecord(
            pid=int(raw["pid"]), port=int(raw["port"]), started_at=str(raw["started_at"])
        )
    except (OSError, ValueError, KeyError):
        return None
    if not pid_alive(record.pid):
        if prune_stale:
            path.unlink(missing_ok=True)
        return None
    return record


def write_record(state_dir: Path, record: DaemonRecord) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    record_path(state_dir).write_text(
        json.dumps(asdict(record), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def clear_record(state_dir: Path, *, pid: int) -> None:
    """Remove the run record iff it still belongs to this pid."""
    current = read_record(state_dir, prune_stale=False)
    if current is not None and current.pid == pid:
        record_path(state_dir).unlink(missing_ok=True)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def daemon_health(state_dir: Path, *, timeout: float = 2.0) -> dict[str, Any] | None:
    """GET /api/v1/health with the stored token; None when not serving."""
    record = read_record(state_dir)
    token = read_token(state_dir)
    if record is None or token is None:
        return None
    try:
        response = httpx.get(
            f"{daemon_url(record)}/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    payload["pid"] = record.pid
    payload["port"] = record.port
    payload["started_at"] = record.started_at
    return payload


def serve_foreground(app: Any, *, port: int, state_dir: Path, log_level: str = "info") -> None:
    """Serve loopback-only until interrupted; owns the run record meanwhile."""
    import uvicorn

    pid = os.getpid()
    write_record(
        state_dir,
        DaemonRecord(
            pid=pid, port=port, started_at=datetime.now(UTC).replace(microsecond=0).isoformat()
        ),
    )
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level=log_level)
    finally:
        clear_record(state_dir, pid=pid)

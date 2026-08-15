"""The local multi-bundle daemon: one loopback origin for UI, HTTP API, and MCP.

The daemon is one shared process serving every installed bundle — immutable
store trees mounted logically into a common runtime. `build_app` assembles
the Starlette application; `lifecycle` owns the pidfile/health handshake the
CLI drives; `security` owns the bearer token and origin posture.
"""

from oknoll_runtime.daemon.app import build_app
from oknoll_runtime.daemon.lifecycle import (
    DEFAULT_PORT,
    DaemonRecord,
    daemon_health,
    daemon_log_path,
    daemon_url,
    read_record,
    serve_foreground,
)
from oknoll_runtime.daemon.security import ensure_token, read_token
from oknoll_runtime.daemon.service import DaemonService, ServiceError

__all__ = [
    "DEFAULT_PORT",
    "DaemonRecord",
    "DaemonService",
    "ServiceError",
    "build_app",
    "daemon_health",
    "daemon_log_path",
    "daemon_url",
    "ensure_token",
    "read_record",
    "read_token",
    "serve_foreground",
]

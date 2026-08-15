"""Platform-appropriate runtime directories.

Three planes, per ADR-worthy separation of concerns: **data** (content store +
catalog — precious), **cache** (derived indexes — disposable), **state**
(daemon pid/token/logs — machine-local). ``~/.oknoll`` remains config and
secrets only; the runtime never writes there.

``OKNOLL_HOME`` overrides everything by nesting all three planes under one
root — the test suite and hermetic setups depend on that single knob.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "oknoll"
HOME_ENV = "OKNOLL_HOME"


@dataclass(frozen=True, slots=True)
class RuntimeDirs:
    data: Path
    cache: Path
    state: Path


def runtime_dirs() -> RuntimeDirs:
    """Resolve the runtime directories; nothing is created until needed."""
    override = os.environ.get(HOME_ENV)
    if override:
        base = Path(override).expanduser()
        return RuntimeDirs(data=base / "data", cache=base / "cache", state=base / "state")
    return RuntimeDirs(
        data=platformdirs.user_data_path(APP_NAME),
        cache=platformdirs.user_cache_path(APP_NAME),
        state=platformdirs.user_state_path(APP_NAME),
    )

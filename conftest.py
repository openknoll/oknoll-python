"""Workspace-wide test isolation: never let a developer's real ~/.oknoll leak in.

Fresh `oknoll init` scaffolds leave model/embedder unset, so resolution falls
through to ~/.oknoll/config.toml — on a developer machine that could point the
suite at a real provider or inject real API keys via ~/.oknoll/.env. Every test
gets an empty OKNOLL_HOME instead; tests that exercise the global layer write
into it explicitly.

The fixture also snapshots os.environ and restores it after every test:
`load_env`/`apply_global_env` mutate the process environment via setdefault
(out-of-band of monkeypatch), so without the restore a value injected in one
test — OLLAMA_HOST especially — would leak into every later test.

This lives at the repo root (not in a package tests/ dir) because a second
tests/conftest.py would collide with okf-core's under mypy's duplicate-module
rule; here pytest still applies it to every package while `mypy packages`
never sees it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_oknoll_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    snapshot = os.environ.copy()
    home = tmp_path_factory.mktemp("oknoll-home")
    os.environ["OKNOLL_HOME"] = str(home)
    try:
        yield home
    finally:
        os.environ.clear()
        os.environ.update(snapshot)

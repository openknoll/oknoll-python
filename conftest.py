"""Workspace-wide test isolation: never let a developer's real ~/.oknoll leak in.

Fresh `oknoll init` scaffolds leave model/embedder unset, so resolution falls
through to ~/.oknoll/config.toml — on a developer machine that could point the
suite at a real provider or inject real API keys via ~/.oknoll/.env. Every test
gets an empty OKNOLL_HOME instead; tests that exercise the global layer write
into it explicitly.

This lives at the repo root (not in a package tests/ dir) because a second
tests/conftest.py would collide with okf-core's under mypy's duplicate-module
rule; here pytest still applies it to every package while `mypy packages`
never sees it.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_oknoll_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    home = tmp_path_factory.mktemp("oknoll-home")
    monkeypatch.setenv("OKNOLL_HOME", str(home))
    return home

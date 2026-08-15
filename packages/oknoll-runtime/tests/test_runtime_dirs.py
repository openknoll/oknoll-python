"""Directory resolution: OKNOLL_HOME nests all planes; platformdirs otherwise."""

from pathlib import Path

import pytest
from oknoll_runtime import runtime_dirs


def test_oknoll_home_nests_all_three_planes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OKNOLL_HOME", str(tmp_path / "home"))
    dirs = runtime_dirs()
    assert dirs.data == tmp_path / "home" / "data"
    assert dirs.cache == tmp_path / "home" / "cache"
    assert dirs.state == tmp_path / "home" / "state"


def test_platformdirs_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OKNOLL_HOME", raising=False)
    dirs = runtime_dirs()
    assert {dirs.data, dirs.cache, dirs.state} != {Path()}
    assert all("oknoll" in str(p) for p in (dirs.data, dirs.cache, dirs.state))
    # Nothing is created by resolution alone.

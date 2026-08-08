"""End-to-end CLI flow for the Phase 2 exit gate:
init → add → build → pack → diff --check, all local, all deterministic (stub model).
"""

import contextlib
import shutil
import tarfile
from pathlib import Path

import pytest
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK = REPO_ROOT / "fixtures" / "sources" / "handbook"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    result = runner.invoke(app, ["init", str(tmp_path), "--name", "handbook"])
    assert result.exit_code == 0, _output(result)
    shutil.copytree(HANDBOOK, tmp_path / "sources" / "handbook")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_full_local_flow(project: Path) -> None:
    result = runner.invoke(app, ["add", "sources/handbook"])
    assert result.exit_code == 0, _output(result)
    assert "registered files source: sources/handbook" in _output(result)
    assert "[[sources]]" in (project / "oknoll.toml").read_text(encoding="utf-8")

    again = runner.invoke(app, ["add", "sources/handbook"])
    assert again.exit_code == 0
    assert "already registered" in _output(again)

    build = runner.invoke(app, ["build"])
    assert build.exit_code == 0, _output(build)
    assert "published revision rev-" in _output(build)
    current = (project / "bundle" / ".oknoll" / "current").read_text(encoding="utf-8").strip()

    rebuild = runner.invoke(app, ["build"])
    assert rebuild.exit_code == 0, _output(rebuild)
    assert "no changes" in _output(rebuild)

    diff = runner.invoke(app, ["diff", "--check"])
    assert diff.exit_code == 0, _output(diff)
    assert "reproducible" in _output(diff)

    lint = runner.invoke(app, ["lint", "bundle"])
    assert lint.exit_code == 0, _output(lint)
    assert "0 error(s), 0 warning(s), 0 info" in _output(lint)

    pack = runner.invoke(app, ["pack"])
    assert pack.exit_code == 0, _output(pack)
    archive = project / "dist" / f"handbook-{current}.tar.gz"
    assert archive.is_file()
    assert archive.with_name(archive.name + ".sha256").is_file()

    first_bytes = archive.read_bytes()
    repack = runner.invoke(app, ["pack"])
    assert repack.exit_code == 0, _output(repack)
    assert archive.read_bytes() == first_bytes  # deterministic archive

    plain = runner.invoke(app, ["pack", "--profile", "plain"])
    assert plain.exit_code == 0, _output(plain)
    plain_archive = project / "dist" / f"handbook-{current}-plain.tar.gz"
    with tarfile.open(plain_archive, mode="r:gz") as tar:
        names = tar.getnames()
    assert all(".oknoll" not in name for name in names)


def test_build_without_sources_fails(project: Path) -> None:
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "no sources registered" in _output(result)


def test_diff_without_revision_fails(project: Path) -> None:
    assert runner.invoke(app, ["add", "sources/handbook"]).exit_code == 0
    result = runner.invoke(app, ["diff", "--check"])
    assert result.exit_code == 1
    assert "no published revision" in _output(result)


def test_pack_without_revision_fails(project: Path) -> None:
    result = runner.invoke(app, ["pack"])
    assert result.exit_code == 1
    assert "run `oknoll build` first" in _output(result)


def test_add_unsupported_source_fails(project: Path) -> None:
    (project / "data.csv").write_text("a,b\n", encoding="utf-8")
    result = runner.invoke(app, ["add", "data.csv"])
    assert result.exit_code == 1
    assert "cannot use" in _output(result)


def test_add_outside_project_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "whatever.md"])
    assert result.exit_code == 1
    assert "no oknoll.toml" in _output(result)

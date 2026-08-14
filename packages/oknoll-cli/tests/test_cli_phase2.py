"""End-to-end CLI flow for the Phase 2 exit gate:
init → add → build → pack → diff --check, all local, all deterministic (stub model).
"""

import contextlib
import json
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
    assert "health:" in _output(lint)

    lint_json = runner.invoke(app, ["lint", "bundle", "--json"])
    assert lint_json.exit_code == 0, _output(lint_json)
    metrics = json.loads(lint_json.output)["metrics"]
    assert metrics["concepts"] > 0
    assert metrics["source_coverage"]["ratio"] == 1.0  # every built concept is sourced
    assert metrics["uncited_references"]["count"] == 0
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


def _current_revision(project: Path) -> str:
    return (project / "bundle" / ".oknoll" / "current").read_text(encoding="utf-8").strip()


def test_semantic_diff_between_two_revisions(project: Path) -> None:
    assert runner.invoke(app, ["add", "sources/handbook"]).exit_code == 0
    assert runner.invoke(app, ["build"]).exit_code == 0
    rev_a = _current_revision(project)

    # amend a source and rebuild — a second revision to compare against
    doc = project / "sources" / "handbook" / "security.md"
    doc.write_text(
        doc.read_text(encoding="utf-8") + "\nAn amendment about rotation.\n", encoding="utf-8"
    )
    rebuild = runner.invoke(app, ["build"])
    assert rebuild.exit_code == 0, _output(rebuild)
    rev_b = _current_revision(project)
    assert rev_b != rev_a

    result = runner.invoke(app, ["diff", rev_a, rev_b])
    assert result.exit_code == 0, _output(result)
    out = _output(result)
    assert f"{rev_a} → {rev_b}" in out
    assert "concepts" in out and "references" in out and "files" in out

    # one revision id compares against the current revision
    implicit = runner.invoke(app, ["diff", rev_a])
    assert implicit.exit_code == 0, _output(implicit)
    assert f"{rev_a} → {rev_b}" in _output(implicit)

    as_json = runner.invoke(app, ["diff", rev_a, "--json"])
    assert as_json.exit_code == 0, _output(as_json)
    payload = json.loads(as_json.output)
    assert payload["rev_a"] == rev_a
    assert payload["rev_b"] == rev_b
    assert payload["files"]["changed"]  # the amended source moved concept + reference

    same = runner.invoke(app, ["diff", rev_b, rev_b])
    assert same.exit_code == 0, _output(same)
    assert "no content differences" in _output(same)


def test_diff_argument_validation(project: Path) -> None:
    assert runner.invoke(app, ["add", "sources/handbook"]).exit_code == 0
    assert runner.invoke(app, ["build"]).exit_code == 0

    bare = runner.invoke(app, ["diff"])
    assert bare.exit_code == 1
    assert "give a revision id" in _output(bare)

    mixed = runner.invoke(app, ["diff", "rev-000000000abc", "--check"])
    assert mixed.exit_code == 1
    assert "--check takes no revision arguments" in _output(mixed)

    unknown = runner.invoke(app, ["diff", "rev-000000000abc"])
    assert unknown.exit_code == 1
    assert "available" in _output(unknown)


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


def test_generation_version_bump_regenerates_deliberately(project: Path) -> None:
    runner.invoke(app, ["add", "sources/handbook"])
    first = runner.invoke(app, ["build"])
    assert first.exit_code == 0, _output(first)

    # Bump the regeneration knob in oknoll.toml: cached generations are
    # deliberately invalidated, and with the deterministic stub the content —
    # and therefore the revision — is unchanged.
    config_path = project / "oknoll.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "[build]", '[build]\ngeneration_version = "1"', 1
        ),
        encoding="utf-8",
    )
    second = runner.invoke(app, ["build"])
    assert second.exit_code == 0, _output(second)
    assert "no changes" in _output(second)
    assert "0 miss(es)" not in _output(second)  # the cache was rebuilt, not replayed


def test_generation_version_must_be_string_or_int(project: Path) -> None:
    config_path = project / "oknoll.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "[build]", "[build]\ngeneration_version = 1.5", 1
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "generation_version" in _output(result)

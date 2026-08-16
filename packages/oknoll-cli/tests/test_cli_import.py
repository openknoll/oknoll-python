"""`oknoll init --from`: adopt an existing bundle as the first published revision."""

import contextlib
from pathlib import Path

import pytest
from okf_core import pack_bundle
from okf_core.revision import compute_revision_id
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "bundles"
MINIMAL = FIXTURES / "golden" / "minimal"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


def test_init_from_directory_adopts_the_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "imported"
    result = runner.invoke(app, ["init", str(proj), "--from", str(MINIMAL)])
    assert result.exit_code == 0, _output(result)

    revision_id = compute_revision_id(MINIMAL)
    assert f"imported 6 file(s) as revision {revision_id} from minimal" in result.output
    assert "no sources registered yet" in result.output

    config_text = (proj / "oknoll.toml").read_text(encoding="utf-8")
    assert "[[sources]]" not in config_text
    log_text = (proj / "bundle" / "log.md").read_text(encoding="utf-8")
    assert f"- {revision_id} — imported from minimal" in log_text

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["ask", "what is authentication?"])
    assert result.exit_code == 0, _output(result)


def test_init_from_archive_matches_the_directory_import(tmp_path: Path) -> None:
    packed = pack_bundle(MINIMAL, tmp_path / "minimal.tar.gz")
    proj = tmp_path / "imported"
    result = runner.invoke(app, ["init", str(proj), "--from", str(packed.archive_path)])
    assert result.exit_code == 0, _output(result)

    assert "checksum verified: minimal.tar.gz.sha256" in result.output
    assert f"as revision {compute_revision_id(MINIMAL)} from minimal.tar.gz" in result.output


def test_init_from_rejects_a_tampered_archive(tmp_path: Path) -> None:
    packed = pack_bundle(MINIMAL, tmp_path / "minimal.tar.gz")
    packed.archive_path.write_bytes(packed.archive_path.read_bytes() + b"\x00")

    proj = tmp_path / "imported"
    result = runner.invoke(app, ["init", str(proj), "--from", str(packed.archive_path)])
    assert result.exit_code == 1
    assert "checksum mismatch" in _output(result)
    assert not (proj / "oknoll.toml").exists()


def test_init_from_lint_failing_source_publishes_nothing(tmp_path: Path) -> None:
    proj = tmp_path / "imported"
    source = FIXTURES / "malformed" / "missing-type"
    result = runner.invoke(app, ["init", str(proj), "--from", str(source)])
    assert result.exit_code == 1
    assert "import failed lint" in _output(result)
    assert "missing-type" in _output(result)  # findings were printed
    assert not (proj / "oknoll.toml").exists()
    assert not (proj / "bundle").exists()


def test_init_from_never_overwrites(tmp_path: Path) -> None:
    proj = tmp_path / "imported"

    # Existing config without --force fails like plain init.
    proj.mkdir()
    (proj / "oknoll.toml").write_text("[project]\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(proj), "--from", str(MINIMAL)])
    assert result.exit_code == 1
    assert "already exists" in _output(result)

    # A non-empty bundle/ blocks the import even with --force.
    (proj / "bundle").mkdir()
    (proj / "bundle" / "precious.md").write_text("mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(proj), "--from", str(MINIMAL), "--force"])
    assert result.exit_code == 1
    assert "never overwritten" in _output(result)
    assert (proj / "bundle" / "precious.md").read_text(encoding="utf-8") == "mine\n"


def test_build_after_import_requires_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "imported"
    result = runner.invoke(app, ["init", str(proj), "--from", str(MINIMAL)])
    assert result.exit_code == 0, _output(result)
    index_bytes = (proj / "bundle" / "index.md").read_bytes()

    monkeypatch.chdir(proj)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "no sources registered" in _output(result)
    assert (proj / "bundle" / "index.md").read_bytes() == index_bytes

"""CLI surface freeze and behavior of the wired commands."""

import contextlib
from pathlib import Path

import pytest
from oknoll_cli.main import app, keys_app, plugin_app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "bundles"

runner = CliRunner()

FROZEN_COMMANDS = {
    "init",
    "add",
    "build",
    "lint",
    "ask",
    "chat",
    "serve",
    "pack",
    "diff",
    "login",
    "eval",
}
FROZEN_GROUPS = {"plugin", "keys"}
FORBIDDEN = {"sync", "validate", "explore", "export"}


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


def test_command_surface_is_frozen() -> None:
    commands = {c.name or c.callback.__name__ for c in app.registered_commands}  # type: ignore[union-attr]
    groups = {g.name for g in app.registered_groups}
    assert commands == FROZEN_COMMANDS
    assert groups == FROZEN_GROUPS
    assert not (commands | groups) & FORBIDDEN


def test_subcommand_surfaces() -> None:
    plugin = {c.name or c.callback.__name__ for c in plugin_app.registered_commands}  # type: ignore[union-attr]
    keys = {c.name or c.callback.__name__ for c in keys_app.registered_commands}  # type: ignore[union-attr]
    assert plugin == {"list", "inspect", "validate"}
    assert keys == {"create", "list", "revoke"}


def test_stub_commands_fail_clearly() -> None:
    for args in (
        ["login"],
        ["keys", "list"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, f"{args}: {result.exit_code}"
        assert "not implemented yet" in _output(result), args


def test_serve_requires_the_mcp_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 2
    assert "only --mcp" in _output(result)

    # with --mcp but no project (and no --bundle), it fails before serving
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["serve", "--mcp"])
    assert result.exit_code == 1
    assert "no oknoll.toml" in _output(result)


def test_add_cloud_sources_not_yet() -> None:
    result = runner.invoke(app, ["add", "cloud:acme/handbook"])
    assert result.exit_code == 2
    assert "not implemented yet" in _output(result)


def _init(tmp_path: Path) -> Path:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "demo"]).exit_code == 0
    return tmp_path / "oknoll.toml"


def test_add_web_source_registers_canonical_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "HTTPS://Example.COM:443/docs#intro"])
    assert result.exit_code == 0, _output(result)
    text = config_path.read_text(encoding="utf-8")
    assert 'connector = "web"' in text
    assert 'uri = "https://example.com/docs"' in text

    again = runner.invoke(app, ["add", "https://example.com/docs"])
    assert again.exit_code == 0
    assert "already registered" in _output(again)


def test_add_github_source_registers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "git@github.com:acme/handbook.git"])
    assert result.exit_code == 0, _output(result)
    assert "acme/handbook" in _output(result)
    text = config_path.read_text(encoding="utf-8")
    assert 'connector = "github"' in text
    assert 'uri = "git@github.com:acme/handbook.git"' in text


def test_add_non_github_git_source_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "git@gitlab.com:acme/handbook.git"])
    assert result.exit_code == 1
    assert "only GitHub repositories" in _output(result)


def test_lint_golden_passes() -> None:
    result = runner.invoke(app, ["lint", str(FIXTURES / "golden" / "minimal")])
    assert result.exit_code == 0, _output(result)


def test_lint_malformed_fails() -> None:
    result = runner.invoke(app, ["lint", str(FIXTURES / "malformed" / "missing-type")])
    assert result.exit_code == 1
    assert "conformance/missing-type" in _output(result)


def test_lint_strict_escalates_hygiene() -> None:
    bundle = str(FIXTURES / "malformed" / "broken-links")
    assert runner.invoke(app, ["lint", bundle]).exit_code == 0
    assert runner.invoke(app, ["lint", bundle, "--strict"]).exit_code == 1


def test_init_creates_lintable_skeleton(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path), "--name", "demo"])
    assert result.exit_code == 0, _output(result)
    assert (tmp_path / "oknoll.toml").is_file()
    assert (tmp_path / "bundle" / "index.md").is_file()

    lint_result = runner.invoke(app, ["lint", str(tmp_path / "bundle")])
    assert lint_result.exit_code == 0, _output(lint_result)

    again = runner.invoke(app, ["init", str(tmp_path)])
    assert again.exit_code == 1
    assert "already exists" in _output(again)

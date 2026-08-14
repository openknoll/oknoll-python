"""CLI surface freeze (v0.4 resource namespaces) and behavior of the wired commands.

The namespace tree is the frozen contract: per-namespace command sets plus the
namespace list itself are pinned by set equality, and every flat v0.3 command
name must remain a hidden pointer stub that performs no work.
"""

import contextlib
from pathlib import Path

import pytest
import typer
from oknoll_cli.commands.legacy import MOVED
from oknoll_cli.main import NAMESPACES, app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "bundles"

runner = CliRunner()

FROZEN_NAMESPACES = [
    "project",
    "source",
    "bundle",
    "image",
    "registry",
    "auth",
    "daemon",
    "query",
    "mcp",
    "ui",
    "config",
    "system",
]

FROZEN_SURFACE: dict[str, set[str]] = {
    "project": {"init", "build"},
    "source": {"add"},
    "bundle": {
        "lint",
        "diff",
        "pack",
        "unpack",
        "install",
        "uninstall",
        "list",
        "inspect",
        "checkout",
        "connect",
    },
    "image": {"build", "list", "inspect", "tag", "remove", "save", "load", "push", "pull"},
    "registry": {"inspect"},
    "auth": {"login"},
    "daemon": {"start", "stop", "status", "logs", "install", "uninstall"},
    "query": {"ask", "chat"},
    "mcp": {"stdio", "endpoint", "config", "inspect"},
    "ui": {"open"},
    "config": {"list", "get", "set", "unset"},
    "system": {"plugins", "viz", "eval", "doctor"},
}

# Old flat commands: pointer stubs only. No aliases from superseded designs either.
LEGACY = {
    "init",
    "add",
    "build",
    "lint",
    "ask",
    "chat",
    "serve",
    "pack",
    "diff",
    "plugin",
    "login",
    "keys",
    "eval",
    "viz",
    "doctor",
}
FORBIDDEN = {"sync", "validate", "explore", "export"}


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


def _commands(sub_app: typer.Typer) -> set[str]:
    return {c.name or c.callback.__name__ for c in sub_app.registered_commands}  # type: ignore[union-attr]


def test_namespace_list_is_frozen() -> None:
    assert list(NAMESPACES) == FROZEN_NAMESPACES
    registered = {g.name for g in app.registered_groups}
    assert registered == set(FROZEN_NAMESPACES)


def test_per_namespace_surfaces_are_frozen() -> None:
    for name, sub_app in NAMESPACES.items():
        commands = _commands(sub_app) | {g.name for g in sub_app.registered_groups}
        assert commands == FROZEN_SURFACE[name], f"namespace {name!r}"


def test_system_plugins_subcommands() -> None:
    plugins_group = next(g for g in NAMESPACES["system"].registered_groups if g.name == "plugins")
    assert plugins_group.typer_instance is not None
    assert _commands(plugins_group.typer_instance) == {"list", "inspect", "validate"}


def test_top_level_commands_are_exactly_the_legacy_stubs() -> None:
    top_level = {c.name or c.callback.__name__ for c in app.registered_commands}  # type: ignore[union-attr]
    assert top_level == LEGACY == set(MOVED)
    assert not top_level & FORBIDDEN
    assert not {g.name for g in app.registered_groups} & FORBIDDEN
    for command in app.registered_commands:
        assert command.hidden, f"legacy stub {command.name!r} must be hidden"


@pytest.mark.parametrize("old", sorted(LEGACY))
def test_legacy_commands_are_pointer_only(old: str) -> None:
    """Every old flat command prints its replacement and exits 2 — it never works."""
    result = runner.invoke(app, [old])
    assert result.exit_code == 2, f"{old}: {result.exit_code}"
    assert f"moved: use 'oknoll {MOVED[old]}'" in _output(result)

    # arguments and --help are swallowed too: still pointer + exit 2, no work
    with_args = runner.invoke(app, [old, "--help", "whatever"])
    assert with_args.exit_code == 2, old
    assert "moved: use" in _output(with_args)


def test_not_yet_commands_fail_clearly() -> None:
    for args in (
        ["auth", "login"],
        ["bundle", "install", "some.okf.tgz", "--name", "x"],
        ["image", "build", "./bundle"],
        ["daemon", "start"],
        ["ui", "open"],
        ["registry", "inspect", "ref"],
        ["mcp", "config", "--client", "claude"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, f"{args}: {result.exit_code}"
        assert "not implemented yet" in _output(result), args


def test_mcp_stdio_without_project_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["mcp", "stdio"])
    assert result.exit_code == 1
    assert "no oknoll.toml" in _output(result)


def _init(tmp_path: Path) -> Path:
    assert runner.invoke(app, ["project", "init", str(tmp_path), "--name", "demo"]).exit_code == 0
    return tmp_path / "oknoll.toml"


def test_add_web_source_registers_canonical_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "add", "HTTPS://Example.COM:443/docs#intro"])
    assert result.exit_code == 0, _output(result)
    text = config_path.read_text(encoding="utf-8")
    assert 'connector = "web"' in text
    assert 'uri = "https://example.com/docs"' in text

    again = runner.invoke(app, ["source", "add", "https://example.com/docs"])
    assert again.exit_code == 0
    assert "already registered" in _output(again)


def test_add_github_source_registers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "add", "git@github.com:acme/handbook.git"])
    assert result.exit_code == 0, _output(result)
    assert "acme/handbook" in _output(result)
    text = config_path.read_text(encoding="utf-8")
    assert 'connector = "github"' in text
    assert 'uri = "git@github.com:acme/handbook.git"' in text


def test_add_non_github_git_source_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["source", "add", "git@gitlab.com:acme/handbook.git"])
    assert result.exit_code == 1
    assert "only GitHub repositories" in _output(result)


def test_lint_golden_passes() -> None:
    result = runner.invoke(app, ["bundle", "lint", str(FIXTURES / "golden" / "minimal")])
    assert result.exit_code == 0, _output(result)


def test_lint_malformed_fails() -> None:
    result = runner.invoke(app, ["bundle", "lint", str(FIXTURES / "malformed" / "missing-type")])
    assert result.exit_code == 1
    assert "conformance/missing-type" in _output(result)


def test_lint_strict_escalates_hygiene() -> None:
    bundle = str(FIXTURES / "malformed" / "broken-links")
    assert runner.invoke(app, ["bundle", "lint", bundle]).exit_code == 0
    assert runner.invoke(app, ["bundle", "lint", bundle, "--strict"]).exit_code == 1


def test_init_creates_lintable_skeleton(tmp_path: Path) -> None:
    result = runner.invoke(app, ["project", "init", str(tmp_path), "--name", "demo"])
    assert result.exit_code == 0, _output(result)
    assert (tmp_path / "oknoll.toml").is_file()
    assert (tmp_path / "bundle" / "index.md").is_file()

    lint_result = runner.invoke(app, ["bundle", "lint", str(tmp_path / "bundle")])
    assert lint_result.exit_code == 0, _output(lint_result)

    again = runner.invoke(app, ["project", "init", str(tmp_path)])
    assert again.exit_code == 1
    assert "already exists" in _output(again)


def test_viz_renders_golden_bundle(tmp_path: Path) -> None:
    out = tmp_path / "viz.html"
    result = runner.invoke(
        app,
        ["system", "viz", "--bundle", str(FIXTURES / "golden" / "multihop"), "--out", str(out)],
    )
    assert result.exit_code == 0, _output(result)
    assert "4 nodes" in _output(result)
    assert out.is_file()
    assert "OKF bundle graph" in out.read_text(encoding="utf-8")


def test_viz_missing_bundle_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["system", "viz", "--bundle", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "not found" in _output(result)

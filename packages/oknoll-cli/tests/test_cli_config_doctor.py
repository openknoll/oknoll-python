"""`oknoll config list|get|set|unset` and `oknoll doctor` at the CLI boundary.

The autouse root-conftest fixture pins OKNOLL_HOME to an empty tmp dir, so the
global layer starts blank in every test. Nothing here reaches the network: the
only doctor case that probes Ollama points at 127.0.0.1 port 1 (instant refusal).
"""

import contextlib
from pathlib import Path

import pytest
from oknoll_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


@pytest.fixture(autouse=True)
def outside_any_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run in a bare directory so the repo's own files never leak in."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- config ----------------------------------------------------------------


def test_config_list_shows_defaults_outside_a_project() -> None:
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "build.model" in output and "rag.embedder" in output
    assert output.count("stub") == 2
    assert "default" in output


def test_config_set_get_roundtrip(isolated_oknoll_home: Path) -> None:
    result = runner.invoke(app, ["config", "set", "build.model", "ollama:llama3"])
    assert result.exit_code == 0, _output(result)
    assert (isolated_oknoll_home / "config.toml").is_file()

    get = runner.invoke(app, ["config", "get", "build.model"])
    assert get.exit_code == 0
    assert get.output.strip() == "ollama:llama3"

    listing = _output(runner.invoke(app, ["config", "list"]))
    assert "ollama:llama3" in listing
    assert "config.toml" in listing


def test_config_set_rejects_unknown_key_and_bad_value() -> None:
    unknown = runner.invoke(app, ["config", "set", "build.nope", "x"])
    assert unknown.exit_code == 1
    assert "unknown setting" in _output(unknown)

    bad_model = runner.invoke(app, ["config", "set", "build.model", "gpt-42"])
    assert bad_model.exit_code == 1
    assert "unknown model provider" in _output(bad_model)

    bad_embedder = runner.invoke(app, ["config", "set", "rag.embedder", "anthropic"])
    assert bad_embedder.exit_code == 1
    assert "unknown embedding provider" in _output(bad_embedder)


def test_config_set_preserves_hand_added_keys(isolated_oknoll_home: Path) -> None:
    (isolated_oknoll_home / "config.toml").write_text(
        '[custom]\nnote = "hands off"\n\n[build]\nmodel = "stub"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["config", "set", "providers.ollama.host", "http://box:11434"])
    assert result.exit_code == 0, _output(result)
    text = (isolated_oknoll_home / "config.toml").read_text(encoding="utf-8")
    assert 'note = "hands off"' in text
    assert 'model = "stub"' in text
    assert 'host = "http://box:11434"' in text


def test_config_unset_removes_and_reports_missing(isolated_oknoll_home: Path) -> None:
    assert runner.invoke(app, ["config", "set", "build.model", "ollama:llama3"]).exit_code == 0
    removed = runner.invoke(app, ["config", "unset", "build.model"])
    assert removed.exit_code == 0
    assert "removed" in _output(removed)
    assert runner.invoke(app, ["config", "get", "build.model"]).output.strip() == "stub"
    # The empty [build] table is pruned from the rewritten file.
    assert "[build]" not in (isolated_oknoll_home / "config.toml").read_text(encoding="utf-8")

    again = runner.invoke(app, ["config", "unset", "build.model"])
    assert again.exit_code == 0
    assert "was not set" in _output(again)


def test_bundle_setting_beats_global_in_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_oknoll_home: Path
) -> None:
    assert runner.invoke(app, ["config", "set", "build.model", "ollama:llama3"]).exit_code == 0
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "proj"]).exit_code == 0
    config_path = tmp_path / "oknoll.toml"
    text = config_path.read_text(encoding="utf-8").replace('# model = "stub"', 'model = "stub"')
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    listing = _output(runner.invoke(app, ["config", "list"]))
    assert "oknoll.toml" in listing
    line = next(ln for ln in listing.splitlines() if ln.startswith("build.model"))
    assert "stub" in line and "oknoll.toml" in line


# --- doctor ----------------------------------------------------------------


def test_doctor_passes_on_a_clean_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "all checks passed" in output
    assert "project: none" in output


def test_doctor_fails_on_malformed_global_config(isolated_oknoll_home: Path) -> None:
    (isolated_oknoll_home / "config.toml").write_text("[build\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    output = _output(result)
    assert "not valid TOML" in output
    assert "problem(s) found" in output


def test_doctor_requires_anthropic_key_when_model_needs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner.invoke(app, ["config", "set", "build.model", "anthropic"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY: not set" in _output(result)


def test_doctor_warns_on_world_readable_env(
    isolated_oknoll_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    env_path = isolated_oknoll_home / ".env"
    env_path.write_text("# empty\n", encoding="utf-8")
    env_path.chmod(0o644)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, _output(result)
    assert "chmod 600" in _output(result)


def test_doctor_fails_when_needed_ollama_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert runner.invoke(app, ["config", "set", "build.model", "ollama:llama3"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["config", "set", "providers.ollama.host", "http://127.0.0.1:1"]
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "cannot reach Ollama" in _output(result)


def test_doctor_reports_bogus_bundle_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "proj"]).exit_code == 0
    config_path = tmp_path / "oknoll.toml"
    text = config_path.read_text(encoding="utf-8").replace('# model = "stub"', 'model = "bogus"')
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "unknown model provider" in _output(result)

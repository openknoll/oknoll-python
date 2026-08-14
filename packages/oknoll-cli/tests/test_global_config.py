"""Machine-level config layering: ~/.oknoll/config.toml + ~/.oknoll/.env.

Settings precedence (specific wins): CLI flag > bundle oknoll.toml >
~/.oknoll/config.toml > "stub". Secrets stay environment-only:
shell env > project .env > ~/.oknoll/.env.
"""

import contextlib
import os
from pathlib import Path

import pytest
from oknoll_cli import project
from oknoll_cli.global_config import (
    GlobalConfig,
    GlobalConfigError,
    apply_global_env,
    load_global_config,
    oknoll_home,
)
from oknoll_cli.main import app
from oknoll_providers import load_env
from typer.testing import CliRunner

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


# --- home resolution -------------------------------------------------------


def test_oknoll_home_honors_env_override(isolated_oknoll_home: Path) -> None:
    assert oknoll_home() == isolated_oknoll_home


def test_oknoll_home_defaults_to_dotdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OKNOLL_HOME", raising=False)
    assert oknoll_home() == Path.home() / ".oknoll"


# --- config.toml loading ---------------------------------------------------


def test_missing_config_is_empty(tmp_path: Path) -> None:
    assert load_global_config(tmp_path) == GlobalConfig()


def test_full_config_parses(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[build]\nmodel = "ollama:llama3"\n'
        '[rag]\nembedder = "ollama:nomic-embed-text"\n'
        '[providers.ollama]\nhost = "http://box:11434"\n',
        encoding="utf-8",
    )
    config = load_global_config(tmp_path)
    assert config.model == "ollama:llama3"
    assert config.embedder == "ollama:nomic-embed-text"
    assert config.ollama_host == "http://box:11434"


def test_non_string_and_empty_values_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[build]\nmodel = 3\n[rag]\nembedder = ""\n[providers]\nollama = "flat"\n',
        encoding="utf-8",
    )
    assert load_global_config(tmp_path) == GlobalConfig()


def test_malformed_config_raises_clean_error(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[build\n", encoding="utf-8")
    with pytest.raises(GlobalConfigError, match="not valid TOML"):
        load_global_config(tmp_path)


# --- env layering ----------------------------------------------------------


def test_global_dotenv_never_overrides_and_project_beats_global(
    tmp_path: Path, isolated_oknoll_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OKNOLL_TEST_SECRET", raising=False)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".env").write_text("OKNOLL_TEST_SECRET=from-project\n")
    (isolated_oknoll_home / ".env").write_text(
        "OKNOLL_TEST_SECRET=from-home\nOKNOLL_TEST_ONLY_HOME=home\n"
    )
    try:
        # Same order as _load_project: project first, then the global layer.
        load_env(project_dir)
        apply_global_env()
        assert os.environ["OKNOLL_TEST_SECRET"] == "from-project"
        assert os.environ["OKNOLL_TEST_ONLY_HOME"] == "home"
    finally:
        os.environ.pop("OKNOLL_TEST_SECRET", None)
        os.environ.pop("OKNOLL_TEST_ONLY_HOME", None)


def test_ollama_host_defaults_from_config_but_env_wins(
    isolated_oknoll_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_oknoll_home / "config.toml").write_text(
        '[providers.ollama]\nhost = "http://from-config:11434"\n', encoding="utf-8"
    )
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    try:
        apply_global_env()
        assert os.environ["OLLAMA_HOST"] == "http://from-config:11434"
    finally:
        os.environ.pop("OLLAMA_HOST", None)

    monkeypatch.setenv("OLLAMA_HOST", "http://from-shell:11434")
    apply_global_env()
    assert os.environ["OLLAMA_HOST"] == "http://from-shell:11434"


# --- CLI-boundary precedence ----------------------------------------------
# `oknoll project build` resolves the provider before checking sources, so a fresh
# project distinguishes the layers by which error it fails with.


@pytest.fixture()
def empty_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert runner.invoke(app, ["project", "init", str(tmp_path), "--name", "empty"]).exit_code == 0
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_scaffold_leaves_model_unset(empty_project: Path) -> None:
    text = (empty_project / "oknoll.toml").read_text(encoding="utf-8")
    assert '# model = "stub"' in text
    assert '# embedder = "stub"' in text
    config = project.load_project(empty_project)
    assert config.model is None
    assert config.embedder is None


def test_global_default_applies_when_bundle_is_silent(
    empty_project: Path, isolated_oknoll_home: Path
) -> None:
    (isolated_oknoll_home / "config.toml").write_text(
        '[build]\nmodel = "bogus-global"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["project", "build"])
    assert result.exit_code == 1
    assert "unknown model provider" in _output(result)


def test_bundle_setting_beats_global_default(
    empty_project: Path, isolated_oknoll_home: Path
) -> None:
    (isolated_oknoll_home / "config.toml").write_text(
        '[build]\nmodel = "bogus-global"\n', encoding="utf-8"
    )
    config_path = empty_project / "oknoll.toml"
    text = config_path.read_text(encoding="utf-8").replace('# model = "stub"', 'model = "stub"')
    config_path.write_text(text, encoding="utf-8")
    result = runner.invoke(app, ["project", "build"])
    assert result.exit_code == 1
    # Past provider resolution: the bundle's "stub" won over the bogus global.
    assert "no sources registered" in _output(result)


def test_unset_everywhere_falls_back_to_stub(empty_project: Path) -> None:
    result = runner.invoke(app, ["project", "build"])
    assert result.exit_code == 1
    assert "no sources registered" in _output(result)


def test_malformed_global_config_fails_cleanly_at_cli(
    empty_project: Path, isolated_oknoll_home: Path
) -> None:
    (isolated_oknoll_home / "config.toml").write_text("[build\n", encoding="utf-8")
    result = runner.invoke(app, ["project", "build"])
    assert result.exit_code == 1
    output = _output(result)
    assert "not valid TOML" in output
    assert "Traceback" not in output

"""Provider selection at the CLI boundary: `ask --model`, .env loading, clean failures.

Nothing here reaches the network — every case fails (or is satisfied) before a
provider would make an HTTP call.
"""

import contextlib
import os
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


@pytest.fixture()
def empty_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "empty"]).exit_code == 0
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_unknown_model_spec_fails_cleanly(empty_project: Path) -> None:
    result = runner.invoke(app, ["ask", "anything?", "--model", "gpt-42"])
    assert result.exit_code == 1
    output = _output(result)
    assert "unknown model provider" in output
    assert "Traceback" not in output


def test_anthropic_without_key_names_the_env_var(
    empty_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["ask", "anything?", "--model", "anthropic:claude-opus-5"])
    assert result.exit_code == 1
    output = _output(result)
    assert "ANTHROPIC_API_KEY" in output
    assert "Traceback" not in output


def test_dotenv_in_project_root_supplies_the_key(
    empty_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the key in .env, `ask --model anthropic:...` gets past provider
    construction and fails on the *next* check (no published revision) — proof
    that _load_project loaded the file."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (empty_project / ".env").write_text("ANTHROPIC_API_KEY=test-key-from-dotenv\n")
    try:
        result = runner.invoke(app, ["ask", "anything?", "--model", "anthropic:claude-opus-5"])
        assert result.exit_code == 1
        output = _output(result)
        assert "ANTHROPIC_API_KEY" not in output
        assert "run `oknoll build` first" in output
    finally:
        # load_env mutated the process environment out-of-band of monkeypatch.
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_malformed_dotenv_fails_cleanly(
    empty_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (empty_project / ".env").write_bytes(b"OKNOLL_BAD=caf\xe9\n")  # not UTF-8
    result = runner.invoke(app, ["ask", "anything?"])
    assert result.exit_code == 1
    output = _output(result)
    assert "not valid UTF-8" in output
    assert "Traceback" not in output


def test_bad_config_model_fails_before_building(
    empty_project: Path,
) -> None:
    config_path = empty_project / "oknoll.toml"
    text = config_path.read_text(encoding="utf-8").replace('model = "stub"', 'model = "bogus"')
    config_path.write_text(text, encoding="utf-8")
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "unknown model provider" in _output(result)

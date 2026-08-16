"""`ask`/`chat --bundle`: zero-copy use of foreign bundles, no project required."""

import contextlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "bundles"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


@pytest.fixture()
def foreign_bundle(tmp_path: Path) -> Path:
    dest = tmp_path / "acme_retail"
    shutil.copytree(FIXTURES / "upstream" / "acme_retail", dest)
    return dest


def test_ask_bundle_needs_no_project(
    foreign_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # project-less cwd
    result = runner.invoke(
        app, ["ask", "--bundle", str(foreign_bundle), "--json", "what is gross margin?"]
    )
    assert result.exit_code == 0, _output(result)

    payload = json.loads(result.output)
    assert payload["abstained"] or payload["citations"]
    assert payload["trace"]["revision_id"] is None
    assert payload["trace_path"] is not None
    traces = list((foreign_bundle / ".oknoll" / "traces").glob("ask-*.json"))
    assert len(traces) == 1


def test_ask_read_only_bundle_warns_and_answers(
    foreign_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mode = foreign_bundle.stat().st_mode
    os.chmod(foreign_bundle, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    try:
        result = runner.invoke(
            app, ["ask", "--bundle", str(foreign_bundle), "--json", "what is gross margin?"]
        )
        assert result.exit_code == 0, _output(result)
        assert "trace not persisted" in _output(result)
        # The runner interleaves the stderr warning with stdout — parse from the brace.
        payload = json.loads(result.output[result.output.index("{") :])
        assert payload["trace_path"] is None
    finally:
        os.chmod(foreign_bundle, mode)


def test_chat_bundle_is_unpinned_and_resumable(
    foreign_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, ["chat", "--bundle", str(foreign_bundle)], input="what is gross margin?\n\n"
    )
    assert result.exit_code == 0, _output(result)
    assert "revision (none — unpinned)" in result.output

    match = re.search(r"conversation (chat-[A-Za-z0-9_-]+) —", result.output)
    assert match is not None, result.output
    conversation_id = match.group(1)
    state = foreign_bundle / ".oknoll" / "conversations" / f"{conversation_id}.jsonl"
    meta = json.loads(state.read_text(encoding="utf-8").splitlines()[0])
    assert meta["revision_id"] is None

    result = runner.invoke(
        app,
        ["chat", "--bundle", str(foreign_bundle), "--resume", conversation_id],
        input="exit\n",
    )
    assert result.exit_code == 0, _output(result)
    assert f"resumed {conversation_id}" in result.output
    assert "revision (none — unpinned)" in result.output


def test_project_mode_ask_regressions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ask", "anything?"])
    assert result.exit_code == 1
    assert "no oknoll.toml" in _output(result)

    assert runner.invoke(app, ["init", str(tmp_path / "proj")]).exit_code == 0
    monkeypatch.chdir(tmp_path / "proj")
    result = runner.invoke(app, ["ask", "anything?"])
    assert result.exit_code == 1
    assert "run `oknoll build` first" in _output(result)

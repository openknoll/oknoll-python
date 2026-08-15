"""Phase 7 secondary surface: `oknoll chat` (revision-pinned REPL) and
`oknoll plugin list|inspect|validate`."""

import contextlib
import json
import shutil
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
def built_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "handbook"]).exit_code == 0
    shutil.copytree(HANDBOOK, tmp_path / "sources" / "handbook")
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["add", "sources/handbook"]).exit_code == 0
    build = runner.invoke(app, ["build"])
    assert build.exit_code == 0, _output(build)
    return tmp_path


def _conversation_files(project: Path) -> list[Path]:
    return sorted((project / "bundle" / ".oknoll" / "conversations").glob("chat-*.jsonl"))


# -- chat --------------------------------------------------------------------


def test_chat_answers_and_persists_the_conversation(built_project: Path) -> None:
    result = runner.invoke(app, ["chat"], input="How are credentials stored?\nexit\n")
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "hash-only" in output
    assert "concepts/security-policy.md" in output

    files = _conversation_files(built_project)
    assert len(files) == 1
    lines = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    meta, user, assistant = lines[0], lines[1], lines[2]
    assert meta["type"] == "meta"
    assert meta["revision_id"].startswith("rev-")
    assert meta["mode"] == "pd"
    assert user == {"type": "turn", "role": "user", "text": "How are credentials stored?"}
    assert assistant["role"] == "assistant"
    assert "concepts/security-policy.md" in assistant["citations"]
    assert (built_project / assistant["trace_path"]).is_file()


def test_chat_second_turn_carries_the_conversation(built_project: Path) -> None:
    """Turn two answers under the chat prompt: the stub's reply names the prior
    turn count, proving history reached the model as structured context."""
    result = runner.invoke(
        app,
        ["chat"],
        input="How are credentials stored?\nHow are credentials stored?\nexit\n",
    )
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "Based on" in output and "prior turn(s):" in output

    [conversation_file] = _conversation_files(built_project)
    lines = [
        json.loads(line) for line in conversation_file.read_text(encoding="utf-8").splitlines()
    ]
    assistants = [line for line in lines if line.get("role") == "assistant"]
    assert len(assistants) == 2
    assert "prior turn(s):" not in assistants[0]["text"]  # turn one had no history
    assert "1 prior turn(s):" in assistants[1]["text"]


def test_chat_resume_restores_the_conversation_context(built_project: Path) -> None:
    assert runner.invoke(app, ["chat"], input="How are credentials stored?\nexit\n").exit_code == 0
    conversation_id = _conversation_files(built_project)[0].stem

    result = runner.invoke(
        app,
        ["chat", "--resume", conversation_id],
        input="How are credentials stored?\nexit\n",
    )
    assert result.exit_code == 0, _output(result)
    # The resumed turn sees the transcript's prior turn.
    assert "1 prior turn(s):" in _output(result)


def test_chat_resume_continues_the_same_conversation(built_project: Path) -> None:
    assert runner.invoke(app, ["chat"], input="exit\n").exit_code == 0
    conversation_id = _conversation_files(built_project)[0].stem

    result = runner.invoke(
        app,
        ["chat", "--resume", conversation_id],
        input="How are credentials stored?\nexit\n",
    )
    assert result.exit_code == 0, _output(result)
    assert f"resumed {conversation_id}" in _output(result)
    assert len(_conversation_files(built_project)) == 1  # appended, not forked


def test_chat_resume_refuses_a_drifted_revision(built_project: Path) -> None:
    assert runner.invoke(app, ["chat"], input="exit\n").exit_code == 0
    conversation_id = _conversation_files(built_project)[0].stem
    pointer = built_project / "bundle" / ".oknoll" / "current"
    pointer.write_text("rev-000000000bad\n", encoding="utf-8")

    result = runner.invoke(app, ["chat", "--resume", conversation_id], input="exit\n")
    assert result.exit_code == 1
    assert "pinned to revision" in _output(result)
    assert "start a new conversation" in _output(result)


def test_chat_resume_mode_must_match(built_project: Path) -> None:
    assert runner.invoke(app, ["chat", "--mode", "pd"], input="exit\n").exit_code == 0
    conversation_id = _conversation_files(built_project)[0].stem

    result = runner.invoke(app, ["chat", "--resume", conversation_id, "--mode", "rag"])
    assert result.exit_code == 1
    assert "must match" in _output(result)


def test_chat_resume_rejects_traversal_ids(built_project: Path) -> None:
    result = runner.invoke(app, ["chat", "--resume", "../../../etc/passwd"], input="exit\n")
    assert result.exit_code == 1
    assert "invalid conversation id" in _output(result)


def test_chat_resume_reports_corrupt_state_cleanly(built_project: Path) -> None:
    assert runner.invoke(app, ["chat"], input="exit\n").exit_code == 0
    corrupt = _conversation_files(built_project)[0]
    # valid JSON, but not a meta dict — must be a clean error, not a traceback
    corrupt.write_text("123\n", encoding="utf-8")
    result = runner.invoke(app, ["chat", "--resume", corrupt.stem], input="exit\n")
    assert result.exit_code == 1
    assert "corrupt conversation state" in _output(result)

    # non-UTF-8 bytes are handled the same way, not as a UnicodeDecodeError
    corrupt.write_bytes(b"\xff\xfe not utf-8\n")
    result = runner.invoke(app, ["chat", "--resume", corrupt.stem], input="exit\n")
    assert result.exit_code == 1
    assert "corrupt conversation state" in _output(result)


def test_chat_stops_when_the_pinned_revision_drifts_mid_session(
    built_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild mid-chat must not record turns that violate the file's pin."""
    from okf_core import read_current_revision_id
    from oknoll_cli import main as main_mod

    pinned = read_current_revision_id(built_project / "bundle")
    calls = {"n": 0}

    def drifting(bundle_path: Path) -> str | None:
        # startup pin + turn-1 recheck see the pinned revision; turn 2 sees a rebuild
        calls["n"] += 1
        return pinned if calls["n"] <= 2 else "rev-000000000new"

    monkeypatch.setattr(main_mod, "read_current_revision_id", drifting)
    result = runner.invoke(
        app, ["chat"], input="How are credentials stored?\nsecond question\nexit\n"
    )
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "revision changed" in output
    assert "start a new one" in output

    # the second question was refused before it was recorded — one user turn only
    files = _conversation_files(built_project)
    roles = [
        json.loads(line)["role"]
        for line in files[0].read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("type") == "turn"
    ]
    assert roles.count("user") == 1


def test_chat_refuses_multiple_bundles(built_project: Path) -> None:
    result = runner.invoke(app, ["chat", "--bundle", "a", "--bundle", "b"])
    assert result.exit_code == 1
    assert "out of scope" in _output(result)


def test_chat_cloud_bundles_not_yet(built_project: Path) -> None:
    result = runner.invoke(app, ["chat", "--bundle", "cloud:acme/handbook"])
    assert result.exit_code == 2
    assert "not implemented yet" in _output(result)


def test_chat_rejects_unknown_mode(built_project: Path) -> None:
    result = runner.invoke(app, ["chat", "--mode", "stuff"])
    assert result.exit_code == 2
    assert "unknown mode" in _output(result)


def test_chat_needs_a_built_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "empty"]).exit_code == 0
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["chat"], input="exit\n")
    assert result.exit_code == 1
    assert "run `oknoll build` first" in _output(result)


# -- plugin ------------------------------------------------------------------


def test_plugin_list_names_the_first_party_connectors() -> None:
    result = runner.invoke(app, ["plugin", "list"])
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    for name in ("files", "web", "github"):
        assert name in output


def test_plugin_inspect_shows_metadata() -> None:
    result = runner.invoke(app, ["plugin", "inspect", "web"])
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "web" in output
    assert "oknoll_connectors" in output


def test_plugin_inspect_unknown_fails() -> None:
    result = runner.invoke(app, ["plugin", "inspect", "gopher"])
    assert result.exit_code == 1
    assert "no such connector" in _output(result)


@pytest.mark.parametrize("name", ["files", "web", "github"])
def test_plugin_validate_passes_for_shipped_connectors(name: str) -> None:
    result = runner.invoke(app, ["plugin", "validate", name])
    assert result.exit_code == 0, _output(result)
    assert "conforms" in _output(result)

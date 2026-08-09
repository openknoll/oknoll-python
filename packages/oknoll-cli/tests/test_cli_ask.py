"""`oknoll ask` end to end: init → add → build → ask, with citations and a trace.

This is the Phase 4 exit gate at the CLI boundary — the same flow a user runs.
"""

import contextlib
import json
import shutil
from pathlib import Path

import pytest
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK = REPO_ROOT / "fixtures" / "sources" / "handbook"
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"

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


def test_ask_prints_answer_citations_and_trace(built_project: Path) -> None:
    result = runner.invoke(app, ["ask", "How are credentials stored?"])
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "hash-only" in output
    assert "Citations:" in output
    assert "concepts/security-policy.md" in output
    assert "Warnings:" in output  # generated concepts are draft + unverified
    assert "trace: bundle/.oknoll/traces/ask-" in output


def test_ask_mode_rag_runs_the_vector_baseline(built_project: Path) -> None:
    result = runner.invoke(app, ["ask", "How are credentials stored?", "--mode", "rag", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert payload["trace"]["condition"] == "rag"
    assert payload["trace"]["policy"]["embedder"] == "stub"
    assert payload["abstained"] is False
    assert all(c["path"].startswith("references/") for c in payload["citations"])
    assert payload["warnings"] == []  # references carry no trust metadata
    # The vector index landed as derived state, keyed by revision + embedder.
    assert list((built_project / "bundle" / ".oknoll" / "rag-index").glob("rev-*--stub.json"))


def test_ask_rejects_unknown_mode(built_project: Path) -> None:
    result = runner.invoke(app, ["ask", "anything", "--mode", "stuff"])
    assert result.exit_code == 2
    assert "unknown mode" in _output(result)


def test_ask_json_is_machine_readable(built_project: Path) -> None:
    result = runner.invoke(app, ["ask", "How are credentials stored?", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert set(payload) >= {"question", "answer", "citations", "warnings", "trace", "trace_path"}
    assert payload["abstained"] is False
    assert payload["trace"]["condition"] == "pd"
    assert payload["trace"]["model"] == "stub"
    assert payload["trace"]["revision_id"].startswith("rev-")
    assert (built_project / payload["trace_path"]).is_file()


def test_ask_abstains_rather_than_inventing(built_project: Path) -> None:
    result = runner.invoke(app, ["ask", "What is the Zanzibar revenue forecast?", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert payload["abstained"] is True
    assert payload["citations"] == []
    assert "Insufficient evidence" in payload["answer"]


def test_ask_needs_a_built_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "empty"]).exit_code == 0
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ask", "anything?"])
    assert result.exit_code == 1
    assert "run `oknoll build` first" in _output(result)


def test_ask_needs_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ask", "anything?"])
    assert result.exit_code == 1
    assert "no oknoll.toml found" in _output(result)


def test_ask_answers_a_multi_hop_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit-gate question, over the frozen multi-hop bundle."""
    assert runner.invoke(app, ["init", str(tmp_path), "--name", "ops"]).exit_code == 0
    shutil.rmtree(tmp_path / "bundle")
    shutil.copytree(MULTIHOP, tmp_path / "bundle")
    pointer = tmp_path / "bundle" / ".oknoll" / "current"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("rev-frozen000000\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["ask", "Who must sign off a production release?", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert "engineering director" in payload["answer"]
    assert [c["path"] for c in payload["citations"]] == [
        "concepts/release-process.md",
        "concepts/duty-roster.md",
    ]
    assert payload["trace"]["hops"] == [
        {"path": "concepts/duty-roster.md", "via": "concepts/release-process.md"}
    ]

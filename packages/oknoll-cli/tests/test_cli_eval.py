"""`oknoll eval` end to end: benchmark spec → both conditions → report files."""

import contextlib
import json
import shutil
import textwrap
from pathlib import Path

import pytest
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


@pytest.fixture()
def spec(tmp_path: Path) -> Path:
    shutil.copytree(GOLDEN / "multihop", tmp_path / "multihop")
    path = tmp_path / "benchmark.toml"
    path.write_text(
        textwrap.dedent(
            """\
            [benchmark]
            name = "cli-smoke"

            [bundles]
            multihop = "multihop"

            [[questions]]
            id = "q01"
            bundle = "multihop"
            class = "multi-hop"
            question = "Who must sign off a production release?"
            gold_evidence = ["concepts/duty-roster.md", "references/source-001.md"]
            """
        ),
        encoding="utf-8",
    )
    return path


def test_eval_writes_report_and_results(spec: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["system", "eval", str(spec)])
    assert result.exit_code == 0, _output(result)
    assert "descriptive benchmark: cli-smoke" in result.output
    assert "| metric | pd | rag |" in result.output

    results_path = tmp_path / "eval-cli-smoke-results.json"
    report_path = tmp_path / "eval-cli-smoke-report.md"
    assert results_path.is_file() and report_path.is_file()
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    assert payload["model"] == "stub" and payload["embedder"] == "stub"
    assert {r["condition"] for r in payload["rows"]} == {"pd", "rag"}


def test_eval_rejects_a_bad_spec(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[benchmark]\n", encoding="utf-8")
    result = runner.invoke(app, ["system", "eval", str(bad)])
    assert result.exit_code == 1
    assert "oknoll system eval" in _output(result)

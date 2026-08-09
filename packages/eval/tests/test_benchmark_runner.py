"""Benchmark loader, runner, and report over the golden bundles.

This is the deterministic-stub eval path that runs in CI: both conditions,
frozen questions, gold evidence pre-written in the spec, one descriptive table.
"""

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

import pytest
from okf_core import StubEmbeddingProvider, StubModelProvider
from oknoll_eval import BenchmarkError, load_benchmark, render_report, run_benchmark

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"


@pytest.fixture()
def spec(tmp_path: Path) -> Path:
    shutil.copytree(GOLDEN / "multihop", tmp_path / "multihop")
    shutil.copytree(GOLDEN / "minimal", tmp_path / "minimal")
    path = tmp_path / "benchmark.toml"
    path.write_text(
        textwrap.dedent(
            """\
            [benchmark]
            name = "golden-smoke"

            [bundles]
            multihop = "multihop"
            minimal = "minimal"

            [[questions]]
            id = "q01"
            bundle = "multihop"
            class = "multi-hop"
            question = "Who must sign off a production release?"
            gold_evidence = ["concepts/duty-roster.md", "references/source-001.md"]

            [[questions]]
            id = "q02"
            bundle = "minimal"
            class = "lookup"
            question = "How is the modular monolith deployed behind the BFF?"
            gold_evidence = ["concepts/architecture.md", "references/source-001.md"]

            [[questions]]
            id = "q03"
            bundle = "minimal"
            class = "unanswerable"
            question = "What is the quarterly revenue forecast for Zanzibar?"
            gold_evidence = []
            """
        ),
        encoding="utf-8",
    )
    return path


def _run(spec_path: Path) -> dict[str, Any]:
    return run_benchmark(
        load_benchmark(spec_path),
        provider=StubModelProvider(),
        embedder=StubEmbeddingProvider(),
        today="2026-08-06",
        clock=lambda: "2026-08-06T12:00:00Z",
        timer=lambda: 0.0,
    )


def test_runner_covers_every_question_under_both_conditions(spec: Path) -> None:
    results = _run(spec)
    assert results["model"] == "stub" and results["embedder"] == "stub"
    assert [(r["question_id"], r["condition"]) for r in results["rows"]] == [
        ("q01", "pd"),
        ("q01", "rag"),
        ("q02", "pd"),
        ("q02", "rag"),
        ("q03", "pd"),
        ("q03", "rag"),
    ]
    assert set(results["revisions"]) == {"minimal", "multihop"}
    assert results["prompt_versions"]  # provenance for the frozen run


def test_gold_evidence_and_abstention_scoring(spec: Path) -> None:
    rows = {(r["question_id"], r["condition"]): r for r in _run(spec)["rows"]}

    # PD answers the multi-hop question via the concept link; gold hit on the concept.
    assert rows[("q01", "pd")]["gold_hit"] is True
    assert rows[("q01", "pd")]["abstained"] is False
    # RAG cites reference snapshots; the same gold set matches via the reference.
    assert rows[("q01", "rag")]["gold_hit"] is True
    assert rows[("q01", "rag")]["cited_paths"][0].startswith("references/")

    # Unanswerable: abstention is the appropriate outcome in both conditions.
    assert rows[("q03", "pd")]["abstained"] is True
    assert rows[("q03", "pd")]["abstention_appropriate"] is True
    assert rows[("q03", "rag")]["abstention_appropriate"] is rows[("q03", "rag")]["abstained"]


def test_results_are_deterministic_and_json_safe(spec: Path) -> None:
    first, second = _run(spec), _run(spec)
    assert first == second
    assert json.loads(json.dumps(first)) == first


def test_report_renders_the_descriptive_table(spec: Path) -> None:
    report = render_report(_run(spec))
    assert "# PD vs RAG — descriptive benchmark: golden-smoke" in report
    assert "Descriptive comparison only" in report
    assert "| metric | pd | rag |" in report
    assert "gold-evidence hit" in report
    assert "| q01 | multi-hop | pd |" in report
    assert "no statistical tests" in report


def test_loader_rejects_malformed_specs(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    base = "[benchmark]\nname = 'x'\n\n[bundles]\nb = 'b'\n\n"
    cases = {
        "no name": "[bundles]\nb = 'b'\n[[questions]]\nid='q'\nbundle='b'\nquestion='?'\n",
        "unknown bundle": base + "[[questions]]\nid='q'\nbundle='zz'\nquestion='?'\n"
        "gold_evidence=['x']\n",
        "duplicate id": base
        + "[[questions]]\nid='q'\nbundle='b'\nquestion='?'\ngold_evidence=['x']\n"
        + "[[questions]]\nid='q'\nbundle='b'\nquestion='?'\ngold_evidence=['x']\n",
        "missing gold": base + "[[questions]]\nid='q'\nbundle='b'\nquestion='?'\n",
        "gold on unanswerable": base + "[[questions]]\nid='q'\nbundle='b'\nquestion='?'\n"
        "class='unanswerable'\ngold_evidence=['x']\n",
        "bad class": base + "[[questions]]\nid='q'\nbundle='b'\nquestion='?'\n"
        "class='vibes'\ngold_evidence=['x']\n",
    }
    for label, text in cases.items():
        spec_path = tmp_path / "bad.toml"
        spec_path.write_text(text, encoding="utf-8")
        with pytest.raises(BenchmarkError):
            load_benchmark(spec_path)
        del label

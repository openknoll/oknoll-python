"""The published okb-pub-v1 spec stays loadable and its numbering stays honest.

The spec is frozen: questions and gold evidence never change (a revision is a
new spec name). Its gold paths, however, depend on the GitHub connector's
selection — ``DEFAULT_INCLUDE``, the media map, and lexicographic numbering
over the pinned trees. If any of that shifts, every ``source-NNN`` gold path
silently points at the wrong file, which is a spec-freezing event, not a
patch. These tests are the tripwire, fully offline: the committed
``tree-*.txt`` listings are the complete blob inventories at the pinned SHAs,
and replaying the connector's selection over them must reproduce the
committed ``sources-*.tsv`` audit tables byte for byte.
"""

import shutil
from pathlib import Path

import pytest
from oknoll_connectors.github import GitHubConnector
from oknoll_eval.benchmark import Benchmark, load_benchmark

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "okb-pub-v1"

CORPORA = (
    ("handbook", "tree-handbook.txt", "sources-handbook.tsv"),
    ("ripgrep", "tree-ripgrep.txt", "sources-ripgrep.tsv"),
)


def _audit_table(tsv_name: str) -> list[tuple[str, str]]:
    lines = (BENCHMARK_DIR / tsv_name).read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        if line.strip():
            source_id, path = line.split("\t")
            rows.append((source_id, path))
    return rows


@pytest.fixture()
def loaded(tmp_path: Path) -> Benchmark:
    """The real spec, loaded with placeholder bundle dirs (CI builds none)."""
    shutil.copy(BENCHMARK_DIR / "spec.toml", tmp_path / "spec.toml")
    (tmp_path / "handbook" / "bundle").mkdir(parents=True)
    (tmp_path / "ripgrep" / "bundle").mkdir(parents=True)
    return load_benchmark(tmp_path / "spec.toml")


def test_spec_loads_with_the_frozen_question_set(loaded: Benchmark) -> None:
    assert loaded.name == "okb-pub-v1"
    assert len(loaded.questions) == 26
    classes: dict[str, int] = {}
    bundles: dict[str, int] = {}
    for question in loaded.questions:
        classes[question.klass] = classes.get(question.klass, 0) + 1
        bundles[question.bundle] = bundles.get(question.bundle, 0) + 1
    assert classes == {
        "lookup": 8,
        "synthesis": 4,
        "multi-hop": 6,
        "trust": 4,
        "unanswerable": 4,
    }
    assert bundles == {"handbook": 13, "ripgrep": 13}


def test_gold_evidence_resolves_via_the_audit_tables(loaded: Benchmark) -> None:
    tables = {
        "handbook": dict(_audit_table("sources-handbook.tsv")),
        "ripgrep": dict(_audit_table("sources-ripgrep.tsv")),
    }
    for question in loaded.questions:
        for entry in question.gold_evidence:
            source_id = Path(entry).stem  # references/source-NNN.md -> source-NNN
            assert entry == f"references/{source_id}.md", (question.id, entry)
            assert source_id in tables[question.bundle], (question.id, entry)


def test_source_numbering_matches_the_connector_selection() -> None:
    """Replaying selection over the pinned trees reproduces the audit tables.

    Exercises the connector's private ``_selected`` on purpose: the frozen gold
    paths depend on exactly this filter plus lexicographic order, so a change
    to ``DEFAULT_INCLUDE`` or the media map must fail here and force the
    okb-pub-v2 decision instead of silently shifting the numbering.
    """
    for name, tree_name, tsv_name in CORPORA:
        connector = GitHubConnector()
        paths = (BENCHMARK_DIR / tree_name).read_text(encoding="utf-8").splitlines()
        selected = sorted(p for p in paths if connector._selected(p))
        derived = [(f"source-{i + 1:03d}", path) for i, path in enumerate(selected)]
        assert derived == _audit_table(tsv_name), name

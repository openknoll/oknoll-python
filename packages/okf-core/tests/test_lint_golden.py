"""Golden-fixture contract for the five-level lint engine."""

import json
from pathlib import Path

import pytest
from okf_core import LintConfig, lint_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
MALFORMED = sorted(
    p.parent for p in (REPO_ROOT / "fixtures/bundles/malformed").glob("*/expected-findings.json")
)


def test_golden_minimal_is_clean(golden_dir: Path) -> None:
    report = lint_bundle(golden_dir / "minimal")
    assert report.findings == []
    assert report.passed(strict=True)


def test_golden_unknown_fields_is_tolerated(golden_dir: Path) -> None:
    """Foreign bundle with unknown type/keys lints clean (permissive consumer)."""
    report = lint_bundle(golden_dir / "unknown-fields")
    assert report.passed(strict=True)
    assert report.codes() <= {"quality/no-sources"}


@pytest.mark.parametrize("bundle", MALFORMED, ids=lambda p: p.name)
def test_malformed_bundles(bundle: Path) -> None:
    expected = json.loads((bundle / "expected-findings.json").read_text(encoding="utf-8"))
    report = lint_bundle(bundle)
    missing = set(expected["expect_codes"]) - report.codes()
    assert not missing, f"expected findings not produced: {missing}; got {sorted(report.codes())}"
    assert report.passed() is expected["passes_default"]
    assert report.passed(strict=True) is expected["passes_strict"]


def test_nonexistent_bundle_fails() -> None:
    report = lint_bundle(Path("/nonexistent/bundle/path"))
    assert not report.passed()
    assert "conformance/not-a-bundle" in report.codes()


def test_report_is_deterministic(golden_dir: Path) -> None:
    a = lint_bundle(golden_dir / "unknown-fields").to_dict()
    b = lint_bundle(golden_dir / "unknown-fields").to_dict()
    assert json.dumps(a, sort_keys=False) == json.dumps(b, sort_keys=False)


def test_health_metrics_of_a_clean_bundle(golden_dir: Path) -> None:
    metrics = lint_bundle(golden_dir / "minimal").metrics
    assert metrics["concepts"] == 2
    assert metrics["references"] == 1
    assert metrics["source_coverage"] == {"sourced": 2, "total": 2, "ratio": 1.0}
    assert metrics["orphan_concepts"] == {"count": 0, "total": 2, "ratio": 0.0}
    assert metrics["broken_links"]["count"] == 0
    assert metrics["freshness"] == {"dated": 0, "stale": 0}
    assert metrics["uncited_references"] == {"count": 0, "paths": []}


def test_health_metrics_flag_degradation(tmp_path: Path) -> None:
    """Unsourced, orphaned, stale, uncited — each shows up as a metric."""
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "references").mkdir()
    (bundle / "index.md").write_text("---\ntitle: Idx\n---\n\nLinks nothing.\n", encoding="utf-8")
    (bundle / "log.md").write_text("# Revision log\n", encoding="utf-8")
    (bundle / "concepts" / "lonely.md").write_text(
        "---\ntitle: Lonely\ntype: Reference\nstale_after: 2020-01-01\n---\n\nNo sources.\n",
        encoding="utf-8",
    )
    (bundle / "references" / "source-001.md").write_text("# never cited\n", encoding="utf-8")

    metrics = lint_bundle(bundle, LintConfig(today="2026-08-12")).metrics
    assert metrics["source_coverage"] == {"sourced": 0, "total": 1, "ratio": 0.0}
    assert metrics["orphan_concepts"] == {"count": 1, "total": 1, "ratio": 1.0}
    assert metrics["broken_links"] == {"count": 0, "internal_links": 0}
    assert metrics["freshness"] == {"dated": 1, "stale": 1}
    assert metrics["uncited_references"] == {"count": 1, "paths": ["references/source-001.md"]}

    # no injected date → freshness is unknown, nothing counts as stale
    assert lint_bundle(bundle).metrics["freshness"] == {"dated": 1, "stale": 0}


def test_health_ratios_survive_an_empty_bundle(tmp_path: Path) -> None:
    """Zero concepts must not divide by zero — ratios go to None."""
    bundle = tmp_path / "empty"
    bundle.mkdir()
    (bundle / "index.md").write_text("---\ntitle: Idx\n---\n\nEmpty.\n", encoding="utf-8")
    metrics = lint_bundle(bundle).metrics
    assert metrics["concepts"] == 0
    assert metrics["source_coverage"]["ratio"] is None
    assert metrics["orphan_concepts"]["ratio"] is None

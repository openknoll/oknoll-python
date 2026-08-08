"""Golden-fixture contract for the five-level lint engine (design §4.5)."""

import json
from pathlib import Path

import pytest
from okf_core import lint_bundle

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

"""Bundle adoption: verbatim staging, lint-gated publish, deterministic ids."""

from pathlib import Path

import pytest
from okf_core import Explorer, import_bundle, lint_bundle
from okf_core.importer import BundleImportError
from okf_core.indexing import index_dir_for
from okf_core.revision import compare_trees, compute_revision_id, revision_dir

UPSTREAM = Path(__file__).resolve().parents[3] / "fixtures" / "bundles" / "upstream"


def test_import_publishes_golden_minimal(golden_dir: Path, tmp_path: Path) -> None:
    source = golden_dir / "minimal"
    bundle = tmp_path / "bundle"
    outcome = import_bundle(source, bundle, summary="imported from minimal")

    assert outcome.published and outcome.staged_dir is None
    assert outcome.revision_id == compute_revision_id(source)
    assert outcome.file_count == 6
    # Content is byte-verbatim; only the rewritten log and recomputed manifest differ.
    drifted = {path for path, _ in compare_trees(source, bundle)}
    assert drifted <= {"log.md", "manifest.json"}
    assert lint_bundle(bundle).passed()
    assert index_dir_for(bundle, outcome.revision_id).is_dir()


def test_import_is_deterministic(golden_dir: Path, tmp_path: Path) -> None:
    source = golden_dir / "minimal"
    first = import_bundle(source, tmp_path / "a", summary="imported from minimal")
    second = import_bundle(source, tmp_path / "b", summary="imported from minimal")

    assert first.revision_id == second.revision_id
    rev_a = revision_dir(tmp_path / "a", first.revision_id)
    rev_b = revision_dir(tmp_path / "b", second.revision_id)
    assert compare_trees(rev_a, rev_b) == []


def test_import_preserves_foreign_log(tmp_path: Path) -> None:
    source = UPSTREAM / "acme_retail"
    bundle = tmp_path / "bundle"
    outcome = import_bundle(source, bundle, summary="imported from acme_retail")

    assert outcome.published
    foreign_log = (source / "log.md").read_text(encoding="utf-8")
    published_log = (bundle / "log.md").read_text(encoding="utf-8")
    assert published_log.startswith(foreign_log.rstrip("\n"))
    assert published_log.endswith(f"- {outcome.revision_id} — imported from acme_retail\n")

    entries = Explorer(bundle).history()["entries"]
    assert [e["revision_id"] for e in entries] == [outcome.revision_id]


def test_import_lint_gate_blocks_publish(malformed_dir: Path, tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    outcome = import_bundle(
        malformed_dir / "missing-type", bundle, summary="imported from missing-type"
    )

    assert not outcome.published
    assert not outcome.lint_report.passed()
    assert not bundle.exists()  # target untouched on failure
    assert outcome.staged_dir is not None and outcome.staged_dir.is_dir()


def test_import_rejects_empty_source(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BundleImportError, match="no bundle content files"):
        import_bundle(empty, tmp_path / "bundle", summary="imported from empty")
    with pytest.raises(BundleImportError, match="not a directory"):
        import_bundle(tmp_path / "missing", tmp_path / "bundle", summary="x")


def test_imported_upstream_bundle_is_explorable(tmp_path: Path) -> None:
    source = UPSTREAM / "ga4"
    bundle = tmp_path / "bundle"
    outcome = import_bundle(source, bundle, summary="imported from ga4")
    assert outcome.published

    explorer = Explorer(bundle)
    overview = explorer.overview()
    assert overview["revision_pinned"] is True
    listing = explorer.list(".", limit=5)
    assert listing["entries"]
    first = str(listing["entries"][0]["path"])
    assert explorer.read(first)["body"]
    assert explorer.search("events")["results"] is not None

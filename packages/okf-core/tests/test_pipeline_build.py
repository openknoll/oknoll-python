"""Pipeline + revision store: the Phase 2 exit gate in test form.

Files → valid linted bundle → immutable revision, reproducible via the
diff-check path, all with the deterministic model stub. The files connector is
imported from oknoll_connectors — the same pairing the CLI ships.
"""

import json
import shutil
from pathlib import Path

import pytest
from okf_core import (
    BuildOutcome,
    PipelineError,
    PipelineSource,
    SourceRef,
    StubModelProvider,
    build_revision,
    check_reproducibility,
    indexing,
    lint_bundle,
    read_current_revision_id,
    revision_dir,
)
from okf_core.bundle import iter_files
from okf_core.explorer import Explorer
from oknoll_connectors import FetchPolicy, FilesConnector

REPO_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK = REPO_ROOT / "fixtures" / "sources" / "handbook"

FIXED_CLOCK = "2026-08-03T00:00:00Z"


def _sources(project_root: Path) -> list[PipelineSource]:
    return [
        PipelineSource(
            source=SourceRef(connector="files", uri="sources"),
            connector=FilesConnector(base_dir=project_root, clock=lambda: FIXED_CLOCK),
            policy=FetchPolicy(),
        )
    ]


def _build(project_root: Path) -> BuildOutcome:
    return build_revision(
        bundle_dir=project_root / "bundle",
        project_name="handbook",
        sources=_sources(project_root),
        provider=StubModelProvider(),
        clock=lambda: FIXED_CLOCK,
    )


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    shutil.copytree(HANDBOOK, tmp_path / "sources")
    return tmp_path


def test_build_publishes_clean_bundle(project_root: Path) -> None:
    outcome = _build(project_root)
    assert outcome.published and outcome.changed
    assert outcome.lint_report.passed(strict=True)
    assert not outcome.lint_report.findings  # the generated bundle is finding-free

    bundle = project_root / "bundle"
    assert read_current_revision_id(bundle) == outcome.revision_id
    materialized = {f.rel_path for f in iter_files(bundle)}
    assert "index.md" in materialized
    assert "log.md" in materialized
    assert "manifest.json" in materialized
    assert sum(1 for p in materialized if p.startswith("concepts/")) == 5
    assert sum(1 for p in materialized if p.startswith("references/")) == 5

    # An independent lint of the materialized bundle agrees.
    assert lint_bundle(bundle).passed(strict=True)

    report = outcome.report
    assert report["counts"] == {"source_files": 5, "documents": 5, "concepts": 5}
    assert report["provider"] == "stub"
    assert report["graph"]["outbound"]["index.md"]

    run_log = bundle / ".oknoll" / "run-logs" / f"{outcome.revision_id}.json"
    assert json.loads(run_log.read_text(encoding="utf-8"))["revision_id"] == outcome.revision_id


def test_concept_body_digests_every_section(project_root: Path) -> None:
    """Option A (Phase 5): each heading carries its opening prose, not a bare outline."""
    outcome = _build(project_root)
    rev = revision_dir(project_root / "bundle", outcome.revision_id)
    body = (rev / "concepts" / "security-policy.md").read_text(encoding="utf-8")
    assert "**Credentials** — Credentials are stored hash-only." in body
    assert "\n- Credentials" not in body  # the old heading-only bullet outline is gone


def test_rebuild_without_changes_is_idempotent(project_root: Path) -> None:
    first = _build(project_root)
    second = _build(project_root)
    assert second.revision_id == first.revision_id
    assert not second.changed and not second.published
    revisions = (project_root / "bundle" / ".oknoll" / "revisions").iterdir()
    assert [d.name for d in revisions] == [first.revision_id]


def test_independent_builds_are_byte_identical(tmp_path: Path) -> None:
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        shutil.copytree(HANDBOOK, root / "sources")
    outcome_a = _build(tmp_path / "a")
    outcome_b = _build(tmp_path / "b")
    assert outcome_a.revision_id == outcome_b.revision_id

    rev_a = revision_dir(tmp_path / "a" / "bundle", outcome_a.revision_id)
    rev_b = revision_dir(tmp_path / "b" / "bundle", outcome_b.revision_id)
    files_a = {f.rel_path: f.abs_path.read_bytes() for f in iter_files(rev_a)}
    files_b = {f.rel_path: f.abs_path.read_bytes() for f in iter_files(rev_b)}
    assert files_a == files_b


def test_changed_source_creates_new_immutable_revision(project_root: Path) -> None:
    first = _build(project_root)
    first_rev = revision_dir(project_root / "bundle", first.revision_id)
    snapshot = {f.rel_path: f.abs_path.read_bytes() for f in iter_files(first_rev)}

    with (project_root / "sources" / "security.md").open("a", encoding="utf-8") as fh:
        fh.write("\nRotate keys quarterly.\n")
    second = _build(project_root)

    assert second.published and second.revision_id != first.revision_id
    assert read_current_revision_id(project_root / "bundle") == second.revision_id
    # The first revision is untouched.
    assert snapshot == {f.rel_path: f.abs_path.read_bytes() for f in iter_files(first_rev)}
    # The log carries the lineage.
    log = (revision_dir(project_root / "bundle", second.revision_id) / "log.md").read_text(
        encoding="utf-8"
    )
    assert first.revision_id in log and second.revision_id in log


def test_second_build_hits_cache(project_root: Path) -> None:
    first = _build(project_root)
    assert first.report["cache"]["misses"] > 0
    second = _build(project_root)
    assert second.report["cache"]["misses"] == 0
    assert second.report["cache"]["hits"] > 0


def test_check_reproducibility(project_root: Path) -> None:
    _build(project_root)
    current, drift = check_reproducibility(
        bundle_dir=project_root / "bundle",
        project_name="handbook",
        sources=_sources(project_root),
        provider=StubModelProvider(),
        clock=lambda: FIXED_CLOCK,
    )
    assert current == read_current_revision_id(project_root / "bundle")
    assert drift == []


def test_check_reproducibility_requires_a_revision(project_root: Path) -> None:
    with pytest.raises(PipelineError, match="no published revision"):
        check_reproducibility(
            bundle_dir=project_root / "bundle",
            project_name="handbook",
            sources=[],
            provider=StubModelProvider(),
        )


def test_index_stage_builds_the_index_the_explorer_reuses(project_root: Path) -> None:
    """Pipeline stage 7 (§5.3): the explorer must find a ready-built index."""
    outcome = _build(project_root)
    bundle = project_root / "bundle"
    built = indexing.index_dir_for(bundle, outcome.revision_id)
    assert (built / indexing.FTS_NAME).is_file()
    assert (built / indexing.GRAPH_NAME).is_file()

    # The materialized bundle hashes to the published revision, so ensure_index
    # resolves to the pipeline's index rather than rebuilding one.
    assert indexing.ensure_index(bundle) == built
    assert Explorer(bundle).search("credentials")["results"]


def test_index_is_derived_state_and_never_bundle_content(project_root: Path) -> None:
    outcome = _build(project_root)
    bundle = project_root / "bundle"
    assert all(not f.rel_path.startswith(".oknoll") for f in iter_files(bundle))
    revision_files = {f.rel_path for f in iter_files(revision_dir(bundle, outcome.revision_id))}
    assert not any("fts.sqlite" in p or "graph.json" in p for p in revision_files)

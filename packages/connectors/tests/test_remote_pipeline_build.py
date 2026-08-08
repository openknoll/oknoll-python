"""Phase 3 exit gate (implementation plan §4): a public website build is
reproducible, and a repository bundle records the commit SHA — end to end
through the real okf-core pipeline with the deterministic model stub."""

from pathlib import Path

from okf_core import PipelineSource, SourceRef, build_revision, resolve_provider
from oknoll_connectors import FetchPolicy
from test_github_connector import COMMIT_SHA, make_github
from test_web_connector import make_web


def _build(bundle_dir: Path, source: PipelineSource) -> str:
    outcome = build_revision(
        bundle_dir=bundle_dir,
        project_name="remote-fixture",
        sources=[source],
        provider=resolve_provider("stub"),
    )
    assert outcome.lint_report.passed(strict=True), [
        f.code for f in outcome.lint_report.sorted_findings()
    ]
    return outcome.revision_id


def _web_source() -> PipelineSource:
    connector, _ = make_web()
    return PipelineSource(
        source=SourceRef(connector="web", uri="https://example.test/"),
        connector=connector,
        policy=FetchPolicy(),
    )


def _github_source() -> PipelineSource:
    connector, _ = make_github()
    return PipelineSource(
        source=SourceRef(connector="github", uri="https://github.com/acme/handbook"),
        connector=connector,
        policy=FetchPolicy(),
    )


def test_website_build_lints_clean_and_rebuilds_identically(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    first = _build(bundle, _web_source())
    second = _build(bundle, _web_source())  # fresh connector, same content
    assert first == second, "website rebuild must be reproducible"
    index = (bundle / "index.md").read_text(encoding="utf-8")
    assert "concepts/" in index


def test_repository_bundle_records_commit_sha(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    first = _build(bundle, _github_source())
    second = _build(bundle, _github_source())
    assert first == second, "repository rebuild must be reproducible"

    references = sorted((bundle / "references").glob("source-*.md"))
    assert references, "reference snapshots must exist"
    joined = "\n".join(ref.read_text(encoding="utf-8") for ref in references)
    assert COMMIT_SHA in joined, "commit-SHA provenance must be recorded in the bundle"

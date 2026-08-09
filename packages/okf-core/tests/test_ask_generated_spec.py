"""Spec-shaped sources: the answer lives in a later section.

Regression for the thin-concept failure diagnosed in
``docs/notes/2026-08-05-thin-concepts-starve-ask-evidence.md``: a spec document
opens with metadata front-matter, and the substance sits under a later heading.
The per-section digest in ``_write_concept`` plus occurrence tie-breaking in
``_best_excerpt`` must surface that section as evidence — not the metadata, and
not the generated description.
"""

import shutil
from pathlib import Path

import pytest
from okf_core import PipelineSource, SourceRef, StubModelProvider, build_revision
from okf_core.ask import AskResult, answer_question
from oknoll_connectors import FetchPolicy, FilesConnector

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "fixtures" / "sources" / "spec"
FIXED_CLOCK = "2026-08-06T00:00:00Z"
CONCEPT = "concepts/zephyr-wire-protocol.md"


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    shutil.copytree(SPEC, tmp_path / "sources")
    outcome = build_revision(
        bundle_dir=tmp_path / "bundle",
        project_name="spec",
        sources=[
            PipelineSource(
                source=SourceRef(connector="files", uri="sources"),
                connector=FilesConnector(base_dir=tmp_path, clock=lambda: FIXED_CLOCK),
                policy=FetchPolicy(),
            )
        ],
        provider=StubModelProvider(),
        clock=lambda: FIXED_CLOCK,
    )
    assert outcome.published and outcome.lint_report.passed(strict=True)
    return tmp_path / "bundle"


def _ask(bundle: Path, question: str) -> AskResult:
    return answer_question(
        bundle_dir=bundle,
        question=question,
        provider=StubModelProvider(),
        today="2026-08-06",
        clock=lambda: FIXED_CLOCK,
        timer=lambda: 0.0,
    )


def test_answer_in_a_late_section_is_reachable_by_term_overlap(bundle: Path) -> None:
    """The a2k-demo failure in fixture form: 'What is X?' over a spec document."""
    result = _ask(bundle, "What is Zephyr?")

    assert not result.abstained
    # The evidence is the digest of "## 3. What is Zephyr?", quoted by the stub.
    assert "governed telemetry mesh" in result.answer
    assert [c.path for c in result.citations] == [CONCEPT]
    assert result.trace["evidence_paths"] == [CONCEPT]


def test_metadata_front_matter_is_no_longer_the_best_evidence(bundle: Path) -> None:
    result = _ask(bundle, "What is Zephyr?")
    # Version/contract/status boilerplate mentions the term too, but loses the
    # occurrence tie-break to the section that actually answers.
    assert "0.3-draft" not in result.answer
    assert "Contract string" not in result.answer

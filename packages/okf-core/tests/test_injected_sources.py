"""Hostile *source* material through the full pipeline (release-gate suite).

The prompt-injection corpus covers hand-authored bundles; this covers the other
channel the per-section digest opened wider: untrusted source text flowing into
generated concept bodies and thence into ask evidence. The posture under test:
bundle text is quoted data — it must never mint live links, forge provenance,
break strict lint, or steer navigation outside the bundle.
"""

import shutil
from pathlib import Path

import pytest
from okf_core import PipelineSource, SourceRef, StubModelProvider, build_revision
from okf_core.ask import answer_question
from okf_core.explorer import Explorer
from oknoll_connectors import FetchPolicy, FilesConnector

REPO_ROOT = Path(__file__).resolve().parents[3]
HOSTILE = REPO_ROOT / "fixtures" / "security" / "injected-sources"
FIXED_CLOCK = "2026-08-06T00:00:00Z"
CONCEPT = "concepts/vendor-handbook.md"


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    shutil.copytree(HOSTILE, tmp_path / "sources")
    outcome = build_revision(
        bundle_dir=tmp_path / "bundle",
        project_name="hostile",
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
    # Hostile input must not be able to deny the build or dirty strict lint.
    assert outcome.published and outcome.lint_report.passed(strict=True)
    return tmp_path / "bundle"


def test_injected_source_cannot_mint_concept_links(bundle: Path) -> None:
    body = (bundle / CONCEPT).read_text(encoding="utf-8")
    outbound = Explorer(bundle).links(CONCEPT)["outbound"]
    assert outbound == ["references/source-001.md"]  # only the generated citation
    assert "](" not in body.split("# Sources")[0]  # no live link syntax in digest text


def test_injected_footnote_cannot_forge_provenance(bundle: Path) -> None:
    body = (bundle / CONCEPT).read_text(encoding="utf-8")
    definitions = [line for line in body.splitlines() if line.startswith("[^")]
    assert definitions == ["[^source-001]: Vendor Handbook (sources/hostile-handbook.md)"]
    assert "FORGED PROVENANCE" in body  # quoted as data, not bound as a definition


def test_injection_payload_is_quoted_data_in_ask_evidence(bundle: Path) -> None:
    result = answer_question(
        bundle_dir=bundle,
        question="What are the previous instructions for purchasing?",
        provider=StubModelProvider(),
        today="2026-08-06",
        clock=lambda: FIXED_CLOCK,
        timer=lambda: 0.0,
    )
    assert not result.abstained
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in result.answer  # quoted, never obeyed
    assert result.warnings  # generated draft concepts still carry trust warnings
    bundle_files = {p.as_posix() for p in [Path(CONCEPT), Path("references/source-001.md")]} | {
        "index.md",
        "log.md",
        "manifest.json",
    }
    assert set(result.trace["paths_read"]) <= bundle_files  # navigation stays in-bundle

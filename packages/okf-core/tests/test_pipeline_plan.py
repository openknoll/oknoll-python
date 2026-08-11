"""Concept planning, cross-concept links, and the regeneration knob.

The planner makes bounded, cached split decisions: a model may only group
outline sections and name the groups, everything else falls back to one
concept per document. Related links are pure code over source text. The
generation_version knob deliberately invalidates cached generations.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from okf_core import (
    Block,
    BuildOutcome,
    CanonicalDoc,
    PipelineError,
    PipelineSource,
    SourceRef,
    StubModelProvider,
    build_revision,
    read_current_revision_id,
    revision_dir,
)
from okf_core.bundle import iter_files
from okf_core.pipeline import _split_sections, _validate_plan
from oknoll_connectors import FetchPolicy, FilesConnector

FIXED_CLOCK = "2026-08-03T00:00:00Z"

SPEC_MD = """\
# Wire Protocol Spec

The protocol has two independent planes.

## Handshake

Clients open with a HELLO frame carrying the protocol version.

## Authentication

Credentials travel only inside the encrypted channel.

## Framing

Every frame is length-prefixed and checksummed.

## Compression

Payloads may be deflate-compressed after negotiation.
"""

GUIDE_MD = """\
# Operator Guide

Day-two operations for the wire protocol deployment.

## Rollout

Enable framing and compression gradually per shard.

## Monitoring

Watch the handshake failure rate during rollout.
"""

SPLIT_PLAN = {
    "concepts": [
        {"title": "Connection Setup", "sections": [0, 1]},
        {"title": "Frame Encoding", "sections": [2, 3]},
    ]
}


class ScriptedProvider:
    """Deterministic test provider: a fixed concept-plan reply, stub-style text
    otherwise. Counts completions so tests can assert cache behavior."""

    def __init__(self, plan_reply: str, provider_id: str = "scripted") -> None:
        self.id = provider_id
        self.plan_reply = plan_reply
        self.calls: list[str] = []

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        self.calls.append(prompt_id)
        if prompt_id == "concept-plan":
            return self.plan_reply
        return StubModelProvider().complete(prompt_id, payload)


def _write_sources(root: Path, files: dict[str, str]) -> None:
    (root / "sources").mkdir()
    for name, text in files.items():
        (root / "sources" / name).write_text(text, encoding="utf-8")


def _build(root: Path, provider: Any, generation_version: str = "0") -> BuildOutcome:
    return build_revision(
        bundle_dir=root / "bundle",
        project_name="planned",
        sources=[
            PipelineSource(
                source=SourceRef(connector="files", uri="sources"),
                connector=FilesConnector(base_dir=root, clock=lambda: FIXED_CLOCK),
                policy=FetchPolicy(),
            )
        ],
        provider=provider,
        clock=lambda: FIXED_CLOCK,
        generation_version=generation_version,
    )


def _read(root: Path, outcome: BuildOutcome, rel: str) -> str:
    return (revision_dir(root / "bundle", outcome.revision_id) / rel).read_text(encoding="utf-8")


# -- model-planned splits ----------------------------------------------------


def test_scripted_plan_splits_document_into_concepts(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    outcome = _build(tmp_path, ScriptedProvider(json.dumps(SPLIT_PLAN)))

    assert outcome.published
    assert outcome.lint_report.passed(strict=True)
    rev = revision_dir(tmp_path / "bundle", outcome.revision_id)
    concepts = sorted(p.name for p in (rev / "concepts").iterdir())
    assert concepts == ["connection-setup.md", "frame-encoding.md"]

    setup = _read(tmp_path, outcome, "concepts/connection-setup.md")
    encoding = _read(tmp_path, outcome, "concepts/frame-encoding.md")
    # The partition is respected: each slice digests only its own sections.
    assert "**Handshake**" in setup and "**Authentication**" in setup
    assert "**Framing**" not in setup
    assert "**Framing**" in encoding and "**Compression**" in encoding
    assert "**Handshake**" not in encoding
    # The preamble travels with the slice containing section 0.
    assert "two independent planes" in setup
    assert "two independent planes" not in encoding
    # Both slices carry the same source provenance.
    assert "resource: references/source-001.md" in setup
    assert "resource: references/source-001.md" in encoding


def test_split_builds_are_deterministic(tmp_path: Path) -> None:
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        _write_sources(root, {"spec.md": SPEC_MD})
    outcome_a = _build(tmp_path / "a", ScriptedProvider(json.dumps(SPLIT_PLAN)))
    outcome_b = _build(tmp_path / "b", ScriptedProvider(json.dumps(SPLIT_PLAN)))
    assert outcome_a.revision_id == outcome_b.revision_id
    rev_a = revision_dir(tmp_path / "a" / "bundle", outcome_a.revision_id)
    rev_b = revision_dir(tmp_path / "b" / "bundle", outcome_b.revision_id)
    assert {f.rel_path: f.abs_path.read_bytes() for f in iter_files(rev_a)} == {
        f.rel_path: f.abs_path.read_bytes() for f in iter_files(rev_b)
    }


def test_plan_decisions_are_cached_including_fallback(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    provider = ScriptedProvider("this is not a plan")
    first = _build(tmp_path, provider)
    # Unusable plan output falls back to one concept for the whole document.
    rev = revision_dir(tmp_path / "bundle", first.revision_id)
    assert [p.name for p in (rev / "concepts").iterdir()] == ["wire-protocol-spec.md"]
    # The fallback itself is cached: a rebuild replays it without a model call,
    # which is what keeps diff --check green behind a nondeterministic model.
    plan_calls = provider.calls.count("concept-plan")
    second = _build(tmp_path, provider)
    assert provider.calls.count("concept-plan") == plan_calls
    assert second.revision_id == first.revision_id


def test_fenced_plan_json_is_accepted(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    fenced = "```json\n" + json.dumps(SPLIT_PLAN) + "\n```"
    outcome = _build(tmp_path, ScriptedProvider(fenced))
    rev = revision_dir(tmp_path / "bundle", outcome.revision_id)
    assert sorted(p.name for p in (rev / "concepts").iterdir()) == [
        "connection-setup.md",
        "frame-encoding.md",
    ]


def test_stub_keeps_one_concept_per_document(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    outcome = _build(tmp_path, StubModelProvider())
    rev = revision_dir(tmp_path / "bundle", outcome.revision_id)
    assert [p.name for p in (rev / "concepts").iterdir()] == ["wire-protocol-spec.md"]


@pytest.mark.parametrize(
    "concepts",
    [
        "not-a-list",
        [{"title": "A", "sections": [0]}],  # gap: section 1+ unclaimed
        [{"title": "A", "sections": [0, 1, 2, 3]}, {"title": "B", "sections": [3]}],  # overlap
        [{"title": "", "sections": [0, 1, 2, 3]}],  # empty title
        [{"title": "A", "sections": [1, 0, 2, 3]}],  # not sorted
        [{"title": "A", "sections": [0, 1, 2, True]}],  # bool masquerading as int
        [{"title": "A", "sections": []}],  # empty section list
    ],
)
def test_validate_plan_rejects_non_partitions(concepts: Any) -> None:
    assert _validate_plan(concepts, 4) is None


def test_validate_plan_accepts_partition_and_keep_whole() -> None:
    assert _validate_plan([], 4) == []
    plan = _validate_plan(
        [{"title": "A", "sections": [0, 2]}, {"title": "B", "sections": [1, 3]}], 4
    )
    assert plan == [{"title": "A", "sections": [0, 2]}, {"title": "B", "sections": [1, 3]}]


def test_validate_plan_strips_links_from_titles() -> None:
    plan = _validate_plan([{"title": "[Click](https://evil.example)", "sections": [0]}], 1)
    assert plan is not None
    assert plan[0]["title"] == "Click"


def test_split_sections_mirrors_digest_boundaries() -> None:
    doc = CanonicalDoc(
        id="doc-x",
        source_ref=SourceRef(connector="files", uri="spec.md"),
        source_hash="0" * 64,
        retrieved_at=FIXED_CLOCK,
        title="Spec",
        blocks=(
            Block(kind="heading", text="Spec", level=1),
            Block(kind="paragraph", text="Preamble prose."),
            Block(kind="heading", text="One", level=2),
            Block(kind="paragraph", text="First."),
            Block(kind="heading", text="Two", level=2),
            Block(kind="paragraph", text="Second."),
        ),
    )
    preamble, sections = _split_sections(doc)
    assert [b.text for b in preamble] == ["Spec", "Preamble prose."]
    assert [heading for heading, _ in sections] == ["One", "Two"]
    assert [b.text for b in sections[0][1]] == ["One", "First."]


def test_hostile_plan_titles_cannot_traverse_or_link(tmp_path: Path) -> None:
    """Planner output is untrusted: titles are link-stripped and slugified, so
    they can never mint live Markdown or escape concepts/."""
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    hostile = {
        "concepts": [
            {"title": "../../etc/passwd", "sections": [0, 1]},
            {"title": "[Exfil](https://evil.example/x)", "sections": [2, 3]},
        ]
    }
    outcome = _build(tmp_path, ScriptedProvider(json.dumps(hostile)))
    assert outcome.lint_report.passed(strict=True)
    rev = revision_dir(tmp_path / "bundle", outcome.revision_id)
    names = sorted(p.name for p in (rev / "concepts").iterdir())
    assert names == ["etc-passwd.md", "exfil.md"]
    index = (rev / "index.md").read_text(encoding="utf-8")
    assert "evil.example" not in index
    assert "](https://" not in index


# -- related links -----------------------------------------------------------


def test_related_links_connect_concepts_across_sources(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"guide.md": GUIDE_MD, "spec.md": SPEC_MD})
    outcome = _build(tmp_path, StubModelProvider())
    assert outcome.lint_report.passed(strict=True)

    # The guide's text mentions "wire protocol", so it cross-references the spec.
    guide = _read(tmp_path, outcome, "concepts/operator-guide.md")
    assert "# Related" in guide
    assert "[Wire Protocol Spec](/concepts/wire-protocol-spec.md)" in guide
    # The edge lands in the link graph the explorer serves.
    outbound = outcome.report["graph"]["outbound"]["concepts/operator-guide.md"]
    assert "concepts/wire-protocol-spec.md" in outbound


def test_no_related_section_without_a_match(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    outcome = _build(tmp_path, StubModelProvider())
    body = _read(tmp_path, outcome, "concepts/wire-protocol-spec.md")
    assert "# Related" not in body


# -- failure resilience ------------------------------------------------------


class FlakyProvider(ScriptedProvider):
    """Fails concept-description for one title (a refused model call) until told
    otherwise."""

    def __init__(self, plan_reply: str, fail_title: str) -> None:
        super().__init__(plan_reply)
        self.fail_title = fail_title

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        if prompt_id == "concept-description" and payload.get("title") == self.fail_title:
            self.calls.append(prompt_id)
            raise RuntimeError("the model declined this request (category: cyber)")
        return super().complete(prompt_id, payload)


def test_failed_build_keeps_cache_so_retry_is_incremental(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"guide.md": GUIDE_MD, "spec.md": SPEC_MD})
    provider = FlakyProvider(json.dumps(SPLIT_PLAN), fail_title="Frame Encoding")

    # The error names the document whose generation failed.
    with pytest.raises(PipelineError, match="concept-description failed for 'Frame Encoding'"):
        _build(tmp_path, provider)

    # Everything that succeeded before the failure survived to disk.
    cache_file = tmp_path / "bundle" / ".oknoll" / "cache" / "build-cache.json"
    assert cache_file.is_file()
    assert "concepts" in cache_file.read_text(encoding="utf-8")

    # The retry replays every cached decision and re-asks only for the one
    # field that never succeeded.
    calls_before = len(provider.calls)
    provider.fail_title = ""
    outcome = _build(tmp_path, provider)
    assert outcome.published
    assert len(provider.calls) - calls_before == 1


# -- generation_version knob -------------------------------------------------


def test_generation_version_bump_regenerates(tmp_path: Path) -> None:
    _write_sources(tmp_path, {"spec.md": SPEC_MD})
    provider = ScriptedProvider(json.dumps(SPLIT_PLAN))
    first = _build(tmp_path, provider, generation_version="0")
    calls_after_first = len(provider.calls)

    # Same version: everything replays from cache.
    second = _build(tmp_path, provider, generation_version="0")
    assert len(provider.calls) == calls_after_first
    assert second.report["cache"]["misses"] == 0

    # Bumped version: every model decision is deliberately re-made.
    third = _build(tmp_path, provider, generation_version="1")
    assert len(provider.calls) > calls_after_first
    assert third.report["cache"]["misses"] > 0
    assert third.report["generation_version"] == "1"
    # The provider is deterministic, so regeneration reproduces the content
    # and the revision id is unchanged — only the cache was rebuilt.
    assert third.revision_id == first.revision_id
    assert read_current_revision_id(tmp_path / "bundle") == first.revision_id

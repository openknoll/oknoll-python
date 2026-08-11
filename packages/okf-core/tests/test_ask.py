"""`ask`: navigation policy, answer contract, and the trace record.

The Phase 4 exit gate lives in ``test_multi_hop_question_is_answered_with_citations``:
a question whose answer is only reachable by following a concept link, answered
with qualified path citations and a complete trace.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from okf_core import StubModelProvider
from okf_core.ask import (
    DEFAULT_TOKEN_BUDGET,
    MAX_LINK_FANOUT,
    AskResult,
    _best_excerpt,
    answer_question,
    write_trace,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"
TODAY = "2026-08-05"
MULTIHOP_QUESTION = "Who must sign off a production release?"


@pytest.fixture()
def multihop(tmp_path: Path) -> Path:
    dest = tmp_path / "multihop"
    shutil.copytree(GOLDEN / "multihop", dest)
    return dest


@pytest.fixture()
def minimal(tmp_path: Path) -> Path:
    dest = tmp_path / "minimal"
    shutil.copytree(GOLDEN / "minimal", dest)
    return dest


def _ask(bundle: Path, question: str, **kwargs: Any) -> AskResult:
    return answer_question(
        bundle_dir=bundle,
        question=question,
        provider=StubModelProvider(),
        today=TODAY,
        clock=lambda: "2026-08-05T12:00:00Z",
        timer=lambda: 0.0,
        **kwargs,
    )


def test_multi_hop_question_is_answered_with_citations(multihop: Path) -> None:
    """Phase 4 exit gate."""
    result = _ask(multihop, MULTIHOP_QUESTION)

    # The second hop's content is in the answer, and it is only reachable by
    # following release-process's link — lexical search never surfaces it.
    assert not result.abstained
    assert "engineering director" in result.answer
    assert result.trace["hops"] == [
        {"path": "concepts/duty-roster.md", "via": "concepts/release-process.md"}
    ]
    searched = next(e for e in result.trace["tools"] if e["tool"] == "search")
    assert searched

    cited = [c.path for c in result.citations]
    assert cited == ["concepts/release-process.md", "concepts/duty-roster.md"]
    assert all(c.source_ids == ["source-001"] for c in result.citations)
    assert all("references/source-001.md" in c.resources for c in result.citations)


def test_navigation_policy_starts_at_overview_and_peeks_before_reading(multihop: Path) -> None:
    tools = [e["tool"] for e in _ask(multihop, MULTIHOP_QUESTION).trace["tools"]]
    assert tools[0] == "overview"  # policy: overview is the first call
    assert tools[1] == "search"  # policy: narrow before reading
    assert tools.index("peek") < tools.index("read")  # policy: peek before read


def test_trace_records_the_full_contract(multihop: Path) -> None:
    trace = _ask(multihop, MULTIHOP_QUESTION).trace
    assert set(trace) >= {
        "question",
        "condition",
        "model",
        "revision_id",
        "started_at",
        "latency_ms",
        "budget",
        "tools",
        "paths_read",
        "evidence_paths",
    }
    assert trace["condition"] == "pd"
    assert trace["model"] == "stub"
    assert trace["budget"]["limit_tokens"] == DEFAULT_TOKEN_BUDGET
    assert trace["budget"]["spent_chars"] > 0
    assert trace["budget"]["exhausted"] is False
    assert all({"tool", "args", "chars", "tokens"} == set(e) for e in trace["tools"])
    assert trace["paths_read"] == sorted(trace["paths_read"])
    assert json.loads(json.dumps(trace))  # the trace is JSON-safe by construction


def test_warnings_flag_draft_and_unverified_evidence(multihop: Path) -> None:
    warnings = _ask(multihop, MULTIHOP_QUESTION).warnings
    assert any("duty-roster.md: status is draft" in w for w in warnings)
    assert any("duty-roster.md: unverified" in w for w in warnings)
    # release-process is stable and verified, so it draws no trust warning.
    assert not any("release-process.md: status" in w for w in warnings)


def test_stale_evidence_is_warned_next_to_its_claim(multihop: Path) -> None:
    concept = multihop / "concepts" / "release-process.md"
    concept.write_text(
        concept.read_text(encoding="utf-8").replace(
            "status: stable", "status: stable\nstale_after: 2026-01-01"
        ),
        encoding="utf-8",
    )
    warnings = _ask(multihop, MULTIHOP_QUESTION).warnings
    assert any("release-process.md: stale since 2026-01-01" in w for w in warnings)


def test_abstains_and_names_the_gap_when_evidence_is_missing(multihop: Path) -> None:
    result = _ask(multihop, "What is the quarterly revenue forecast for Zanzibar?")
    assert result.abstained
    assert result.citations == []
    assert "Insufficient evidence" in result.answer
    assert "zanzibar" in result.answer.lower()  # names what would resolve the gap
    assert "resolve the gap" in result.answer


def test_answer_is_deterministic_with_the_stub(multihop: Path) -> None:
    first = _ask(multihop, MULTIHOP_QUESTION)
    second = _ask(multihop, MULTIHOP_QUESTION)
    assert first.answer == second.answer
    assert [c.to_dict() for c in first.citations] == [c.to_dict() for c in second.citations]
    assert first.trace["tools"] == second.trace["tools"]


def test_link_fanout_is_bounded(multihop: Path) -> None:
    trace = _ask(multihop, MULTIHOP_QUESTION).trace
    assert trace["policy"]["max_link_fanout"] == MAX_LINK_FANOUT
    assert len(trace["hops"]) <= MAX_LINK_FANOUT


def test_a_spent_budget_stops_navigation(multihop: Path) -> None:
    trace = _ask(multihop, MULTIHOP_QUESTION, budget_tokens=1).trace
    assert trace["budget"]["exhausted"] is True
    assert trace["hops"] == []  # no budget left to follow links
    tools = [e["tool"] for e in trace["tools"]]
    assert "peek" not in tools  # peeks are budgeted work, not free preamble


def test_best_excerpt_selection_rule_is_pinned() -> None:
    """Distinct term coverage first; total occurrences break ties; then earliest.

    The occurrence tie-break is what lets a single-salient-term question reach
    the section that answers it instead of the first passing mention. It is
    deliberately repetition-sensitive — bundle text can game it, which is the
    same trust level as lexical search ranking (evidence stays quoted data, and
    the trace names every excerpt's path).
    """
    terms = {"zephyr", "mesh"}
    body = (
        "Zephyr appears here once.\n\n"
        "Zephyr is a governed mesh; the Zephyr mesh replicates frames.\n\n"
        "Zephyr and mesh, later and denser: mesh mesh mesh Zephyr Zephyr."
    )
    excerpt, score = _best_excerpt(body, terms)
    assert score == 2
    assert excerpt.startswith("Zephyr and mesh, later and denser")  # occurrences win the tie

    earliest, _ = _best_excerpt("alpha zephyr.\n\nbeta zephyr.", {"zephyr"})
    assert earliest == "alpha zephyr."  # equal keys: earliest still wins

    assert _best_excerpt("no match here.", terms) == ("", 0)


def test_reference_snapshots_do_not_double_cite_their_concept(minimal: Path) -> None:
    result = _ask(minimal, "How is the modular monolith deployed behind the BFF?")
    paths = [c.path for c in result.citations]
    assert "concepts/architecture.md" in paths
    assert "references/source-001.md" not in paths  # folded into its concept's citation


def test_unknown_retrieval_condition_is_rejected(multihop: Path) -> None:
    with pytest.raises(ValueError, match="unknown retrieval condition"):
        _ask(multihop, MULTIHOP_QUESTION, condition="stuff")


def test_write_trace_is_derived_state_outside_the_bundle(multihop: Path) -> None:
    result = _ask(multihop, MULTIHOP_QUESTION)
    path = write_trace(multihop, result)
    assert path.is_file()
    assert ".oknoll/traces" in path.as_posix()  # derived state, never bundle content
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["question"] == MULTIHOP_QUESTION
    assert saved["trace"]["evidence_paths"] == result.trace["evidence_paths"]

    second = write_trace(multihop, result)
    assert second != path  # a repeated ask never overwrites an earlier trace


def test_seed_defining_the_term_outranks_anchor_matched_hops(multihop: Path) -> None:
    """The A2K regression: a hop's excerpt may be *chosen* via its anchor text,
    but ranking counts question-term hits only (navigation order breaks ties) —
    otherwise anchor-matched hops displace the very seed that defines the term."""
    result = _ask(multihop, "What is the release process?")
    assert not result.abstained
    cited = [c.path for c in result.citations]
    assert cited[0] == "concepts/release-process.md"
    # The hop still contributes evidence — ranked after the seed, not instead of it.
    assert "concepts/duty-roster.md" in cited

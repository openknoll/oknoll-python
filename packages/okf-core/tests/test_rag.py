"""RAG condition: chunker, stub embedder, index cache, retrieval, and the
`condition="rag"` answer path (design §9.2 RAG-k, ADR-0005).

The contract under test: RAG answers over the identical normalized corpus
(reference snapshots), shares PD's answer contract and trace shape, runs on
equal per-passage evidence budgets, and is fully deterministic with the stub
embedder + stub model.
"""

import json
import math
import shutil
from pathlib import Path
from typing import Any

import pytest
from okf_core import StubEmbeddingProvider, StubModelProvider, resolve_embedder
from okf_core.ask import EXCERPT_CHARS, AskResult, answer_question
from okf_core.rag import (
    CHUNK_CHARS,
    RAG_INDEX_DIR,
    Chunk,
    chunk_references,
    ensure_rag_index,
    retrieve,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"


@pytest.fixture()
def minimal(tmp_path: Path) -> Path:
    dest = tmp_path / "minimal"
    shutil.copytree(GOLDEN / "minimal", dest)
    return dest


def _ask_rag(bundle: Path, question: str, **kwargs: Any) -> AskResult:
    return answer_question(
        bundle_dir=bundle,
        question=question,
        provider=StubModelProvider(),
        condition="rag",
        today="2026-08-06",
        clock=lambda: "2026-08-06T12:00:00Z",
        timer=lambda: 0.0,
        **kwargs,
    )


# -- chunker and embedder ----------------------------------------------------


def test_chunk_size_matches_the_ask_excerpt_budget() -> None:
    """Equal evidence budgets: one RAG chunk == one PD evidence excerpt."""
    assert CHUNK_CHARS == EXCERPT_CHARS


def test_chunker_is_deterministic_and_bounded(minimal: Path) -> None:
    first = chunk_references(minimal)
    second = chunk_references(minimal)
    assert first == second
    assert first  # the golden bundle has reference bodies
    assert all(len(c.text) <= CHUNK_CHARS for c in first)
    assert all(c.path.startswith("references/") for c in first)
    # Chunk ordinals are per-file document order.
    for path in {c.path for c in first}:
        seqs = [c.seq for c in first if c.path == path]
        assert seqs == list(range(len(seqs)))


def test_chunker_hard_splits_oversized_paragraphs(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir(parents=True)
    (refs / "source-001.md").write_text(
        "---\ntitle: Big\n---\n\n" + "x" * (CHUNK_CHARS * 2 + 100), encoding="utf-8"
    )
    chunks = chunk_references(tmp_path)
    assert [len(c.text) for c in chunks] == [CHUNK_CHARS, CHUNK_CHARS, 100]


def test_stub_embedder_is_deterministic_and_normalized() -> None:
    embedder = StubEmbeddingProvider()
    [a1] = embedder.embed(["deploy the modular monolith"])
    [a2] = embedder.embed(["deploy the modular monolith"])
    [b] = embedder.embed(["completely unrelated words"])
    assert a1 == a2
    assert math.isclose(sum(v * v for v in a1), 1.0, rel_tol=1e-9)
    similarity = sum(x * y for x, y in zip(a1, b, strict=True))
    assert similarity < 0.5  # unrelated texts are far apart


def test_resolve_embedder_knows_only_the_stub() -> None:
    assert resolve_embedder("stub").id == "stub"
    with pytest.raises(ValueError, match="unknown embedding provider"):
        resolve_embedder("openai:whatever")


def test_retrieval_ties_break_by_path_then_seq() -> None:
    vector = [1.0, 0.0]
    index = {
        "chunks": [
            {"path": "references/b.md", "seq": 0, "text": "t", "vector": vector},
            {"path": "references/a.md", "seq": 1, "text": "t", "vector": vector},
            {"path": "references/a.md", "seq": 0, "text": "t", "vector": vector},
        ]
    }
    hits = retrieve(index, vector, 3)
    assert [(h["path"], h["seq"]) for h in hits] == [
        ("references/a.md", 0),
        ("references/a.md", 1),
        ("references/b.md", 0),
    ]
    assert retrieve(index, [0.0, 1.0], 3) == []  # zero similarity never qualifies


# -- index cache -------------------------------------------------------------


def test_index_is_cached_per_revision_as_derived_state(minimal: Path, tmp_path: Path) -> None:
    # Give the bundle a pinned revision so the cache path is keyed.
    oknoll = minimal / ".oknoll"
    (oknoll / "revisions" / "rev-test").mkdir(parents=True)
    (oknoll / "current").write_text("rev-test\n", encoding="utf-8")

    embedder = StubEmbeddingProvider()
    first = ensure_rag_index(minimal, embedder)
    index_files = list((minimal / RAG_INDEX_DIR).glob("*.json"))
    assert len(index_files) == 1  # derived state, outside bundle content

    # Prove the cache is reused: poison the stored text and read it back.
    data = json.loads(index_files[0].read_text(encoding="utf-8"))
    data["chunks"][0]["text"] = "POISONED"
    index_files[0].write_text(json.dumps(data), encoding="utf-8")
    second = ensure_rag_index(minimal, embedder)
    assert second["chunks"][0]["text"] == "POISONED"
    assert first["revision_id"] == "rev-test"


def test_unpinned_bundles_index_in_memory_only(minimal: Path) -> None:
    ensure_rag_index(minimal, StubEmbeddingProvider())
    assert not (minimal / RAG_INDEX_DIR).exists()


# -- the rag answer path -----------------------------------------------------


def test_rag_answers_with_reference_citations(minimal: Path) -> None:
    result = _ask_rag(minimal, "How is the modular monolith deployed behind the BFF?")
    assert not result.abstained
    assert result.citations
    assert all(c.path.startswith("references/") for c in result.citations)
    assert result.trace["condition"] == "rag"
    assert result.trace["evidence_paths"]
    assert set(result.trace["paths_read"]) == {c.path for c in result.citations}
    assert result.warnings == []  # references carry no trust metadata (RQ3)


def test_rag_trace_shape_matches_pd(minimal: Path) -> None:
    pd = answer_question(
        bundle_dir=minimal,
        question="How is the modular monolith deployed behind the BFF?",
        provider=StubModelProvider(),
        today="2026-08-06",
        clock=lambda: "2026-08-06T12:00:00Z",
        timer=lambda: 0.0,
    )
    rag = _ask_rag(minimal, "How is the modular monolith deployed behind the BFF?")
    assert set(rag.trace) == set(pd.trace)  # identical trace shape
    assert set(rag.trace["budget"]) == set(pd.trace["budget"])
    assert rag.trace["hops"] == []
    assert json.loads(json.dumps(rag.trace))  # JSON-safe
    assert {"tool", "args", "chars", "tokens"} == set(rag.trace["tools"][0])


def test_rag_evidence_respects_the_budget_caps(minimal: Path) -> None:
    result = _ask_rag(minimal, "How is the modular monolith deployed behind the BFF?")
    assert len(result.trace["evidence_paths"]) <= 4
    assert result.trace["policy"]["k"] == 4
    assert result.trace["budget"]["spent_chars"] <= 4 * CHUNK_CHARS + len(result.question)

    tiny = _ask_rag(minimal, "How is the modular monolith deployed?", budget_tokens=1)
    assert tiny.trace["budget"]["exhausted"] is True
    assert tiny.abstained  # nothing fit the budget


def test_rag_abstains_when_nothing_matches(minimal: Path) -> None:
    result = _ask_rag(minimal, "Qqqxyzzy plugh frobnicate?")
    assert result.abstained
    assert result.citations == []
    assert "Insufficient evidence" in result.answer
    assert "resolve the gap" in result.answer


def test_rag_is_deterministic_with_the_stubs(minimal: Path) -> None:
    first = _ask_rag(minimal, "How is the modular monolith deployed behind the BFF?")
    second = _ask_rag(minimal, "How is the modular monolith deployed behind the BFF?")
    assert first.answer == second.answer
    assert first.trace == second.trace
    assert [c.to_dict() for c in first.citations] == [c.to_dict() for c in second.citations]


def test_chunk_dataclass_is_frozen() -> None:
    chunk = Chunk(path="references/a.md", seq=0, text="t")
    with pytest.raises(AttributeError):
        chunk.text = "u"  # type: ignore[misc]

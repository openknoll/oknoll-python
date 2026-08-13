"""One-shot explorer answers: navigation policy, answer contract, trace.

Navigation policy is deterministic code, not model output: start at
``overview``, narrow with lexical ``search``, ``peek`` before ``read``, follow
``links`` with bounded fan-out (≤4), stop when the answer is supported or the
token budget (default 25,000 input tokens) is spent. The model only writes the
final prose over retrieved evidence — with the deterministic stub the whole
answer is a pure function of the bundle and the question.

Answer contract: citations carry qualified concept paths plus OKF source
ids and resource links; warnings flag draft/deprecated/stale/unverified
evidence; on insufficient evidence the answer abstains and names what would
resolve the gap. Bundle text is untrusted data — it is quoted, never obeyed.

The trace is a separate record (tools, paths, retrieved characters, token
estimates, latency, model, provider token usage, revision id, retrieval
condition), derived state stored under ``.oknoll/traces/`` — never part of a
portable bundle. ``budget.spent_tokens`` is the *retrieval* budget (an estimate
over retrieved characters); ``model_usage`` is the provider's own accounting
for the answer call (``input_tokens``/``output_tokens``), read from the
provider's ``last_usage`` attribute when it has one — ``None`` for providers
that do not report usage (the stub) and for abstentions, which make no call.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core.canonical import sha256_hex
from okf_core.explorer import Explorer, estimate_tokens
from okf_core.frontmatter import Frontmatter, parse_document
from okf_core.indexing import question_terms, term_occurrences, tokenize
from okf_core.provider import ModelProvider
from okf_core.rag import (
    CHUNK_CHARS,
    EmbeddingProvider,
    ensure_rag_index,
    resolve_embedder,
    retrieve,
)
from okf_core.revision import read_current_revision_id

DEFAULT_TOKEN_BUDGET = 25_000
MAX_LINK_FANOUT = 4
MAX_CONCEPT_READS = 4
MAX_EVIDENCE = 4
EXCERPT_CHARS = 500
TRACES_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/traces"
TRACE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Citation:
    path: str  # qualified bundle path
    title: str
    source_ids: list[str]
    resources: list[str]  # reference snapshot paths or original resource URIs

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "source_ids": self.source_ids,
            "resources": self.resources,
        }


@dataclass(slots=True)
class AskResult:
    question: str
    answer: str
    abstained: bool
    citations: list[Citation]
    warnings: list[str]
    trace: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "abstained": self.abstained,
            "citations": [c.to_dict() for c in self.citations],
            "warnings": self.warnings,
            "trace": self.trace,
        }


@dataclass(slots=True)
class _Traced:
    """Wraps the explorer so every tool call lands in the trace with its cost."""

    explorer: Explorer
    events: list[dict[str, Any]] = field(default_factory=list)
    spent_chars: int = 0

    @property
    def spent_tokens(self) -> int:
        return estimate_tokens(self.spent_chars)

    def call(self, tool: str, /, **kwargs: Any) -> dict[str, Any]:
        method: Callable[..., dict[str, Any]] = getattr(self.explorer, tool)
        result = method(**kwargs)
        chars = len(json.dumps(result, ensure_ascii=False, default=str))
        self.spent_chars += chars
        self.events.append(
            {"tool": tool, "args": kwargs, "chars": chars, "tokens": estimate_tokens(chars)}
        )
        return result


_FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^[^\]]+\]:")
_LINK_TARGET_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _visible_prose(text: str) -> str:
    """Text as a reader sees it: link targets and footnote markers removed.

    Excerpts are scored and quoted over this — a path component such as
    ``security.md`` inside a link target must not outvote actual sentences.
    """
    return _FOOTNOTE_MARKER_RE.sub("", _LINK_TARGET_RE.sub(r"\1", text))


def _candidate_paragraphs(body: str) -> list[str]:
    """Prose paragraphs eligible as excerpts.

    Structural scaffolding never answers a question, so it never competes:
    bare headings, footnote definitions, and everything under a ``Sources``
    heading (the machine-written source list) are skipped. For a title-shaped
    question ("What is the security policy?") that scaffolding repeats the
    question terms densely enough to beat the summary paragraph — the demo
    regression this filter pins.
    """
    candidates: list[str] = []
    in_sources = False
    for raw in body.split("\n\n"):
        para = raw.strip()
        if not para:
            continue
        lines = [line for line in para.splitlines() if line.strip()]
        headings = [m.group(2) for line in lines if (m := _HEADING_RE.match(line))]
        if headings:
            in_sources = headings[-1].strip().lower() == "sources"
        if len(headings) == len(lines):
            continue  # bare heading(s): navigation, not prose
        if in_sources:
            continue
        if all(_FOOTNOTE_DEF_RE.match(line) for line in lines):
            continue
        candidates.append(para)
    return candidates


def _ranked_excerpts(body: str, terms: set[str], description: str = "") -> list[tuple[str, int]]:
    """One document's matching prose excerpts as (excerpt, distinct), best first.

    Order is (distinct term hits, total occurrences) descending, document order
    breaking ties — the head of the list is the document's single best excerpt.
    A single-salient-term question ("What is A2K?") gives every mentioning
    paragraph the same distinct count; occurrence density is what separates the
    section that answers it from a passing mention, and it is just as
    deterministic. Both counts run over the visible prose
    (:func:`_visible_prose`), and only over candidate paragraphs
    (:func:`_candidate_paragraphs`). When no body paragraph matches, the
    frontmatter description — the generated one-paragraph summary — is the sole
    fallback candidate, so a document whose only body hits are scaffolding can
    still contribute its summary rather than a path stub.
    """
    scored: list[tuple[int, int, int, str]] = []
    for position, para in enumerate(_candidate_paragraphs(body)):
        prose = _visible_prose(para)
        distinct = len(terms & tokenize(prose))
        if distinct == 0:
            continue
        scored.append((-distinct, -term_occurrences(prose, terms), position, para))
    scored.sort()
    if not scored and description:
        distinct = len(terms & tokenize(_visible_prose(description)))
        if distinct:
            scored = [(-distinct, 0, 0, description)]
    return [
        (_visible_prose(para).strip()[:EXCERPT_CHARS], -neg_distinct)
        for neg_distinct, _, _, para in scored
    ]


def _best_excerpt(body: str, terms: set[str], description: str = "") -> tuple[str, int]:
    """The head of :func:`_ranked_excerpts`: one document's single best excerpt."""
    ranked = _ranked_excerpts(body, terms, description)
    return ranked[0] if ranked else ("", 0)


def _concept_warnings(path: str, frontmatter: dict[str, Any], today: str | None) -> list[str]:
    """Trust warnings for one cited concept, phrased next to the affected claim."""
    fm = Frontmatter(data=frontmatter)
    warnings: list[str] = []
    if fm.status == "draft":
        warnings.append(f"{path}: status is draft — content is unreviewed")
    elif fm.status == "deprecated":
        warnings.append(f"{path}: status is deprecated — may no longer apply")
    if today and fm.stale_after and fm.stale_after < today:
        warnings.append(f"{path}: stale since {fm.stale_after}")
    if not fm.verified:
        warnings.append(f"{path}: unverified — no verification record")
    return warnings


def _citation_for(path: str, frontmatter: dict[str, Any], title: str) -> Citation:
    fm = Frontmatter(data=frontmatter)
    resources: list[str] = []
    for source in fm.sources:
        resource = source.get("resource")
        if isinstance(resource, str):
            resources.append(resource)
    origin = frontmatter.get("openknoll_source")
    if isinstance(origin, dict) and isinstance(origin.get("uri"), str):
        resources.append(origin["uri"])  # reference snapshots cite their origin
    return Citation(path=path, title=title, source_ids=fm.source_ids(), resources=resources)


def answer_question(
    *,
    bundle_dir: Path,
    question: str,
    provider: ModelProvider,
    today: str | None = None,
    budget_tokens: int = DEFAULT_TOKEN_BUDGET,
    condition: str = "pd",
    embedder: EmbeddingProvider | None = None,
    clock: Callable[[], str] | None = None,
    timer: Callable[[], float] | None = None,
) -> AskResult:
    """Answer one question under a retrieval condition.

    ``pd`` runs the deterministic navigation policy over concepts; ``rag`` runs
    the vector top-k baseline over the identical normalized corpus (reference
    snapshots). Both share the answer contract, the evidence budget
    (≤ MAX_EVIDENCE excerpts of ≤ EXCERPT_CHARS), and the trace shape. PD
    allocates evidence slots diversity-first: each read document's best excerpt
    claims a slot, then spare slots go to remaining paragraphs in rank order —
    mirroring RAG's ability to take several chunks from one document.
    """
    if condition not in ("pd", "rag"):
        raise ValueError(f"unknown retrieval condition {condition!r} — available: 'pd', 'rag'")
    clock = clock or (lambda: datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    timer = timer or time.monotonic
    if condition == "rag":
        return _answer_rag(
            bundle_dir=bundle_dir,
            question=question,
            provider=provider,
            embedder=embedder if embedder is not None else resolve_embedder("stub"),
            budget_tokens=budget_tokens,
            clock=clock,
            timer=timer,
        )
    started_at = clock()
    t0 = timer()

    explorer = Explorer(bundle_dir, today=today)
    traced = _Traced(explorer)
    terms = set(question_terms(question))

    # 1. overview is always the first call (navigation-policy guardrail).
    traced.call("overview")

    # 2. narrow through lexical search. Concepts are the navigation surface;
    #    reference snapshots are raw sources, consulted only when no concept hit.
    search = traced.call("search", query=question)
    results = list(search["results"])
    concept_hits = [str(r["path"]) for r in results if r["kind"] == "concept"]
    reference_hits = [str(r["path"]) for r in results if r["kind"] == "reference"]
    candidates = concept_hits[:MAX_CONCEPT_READS]
    if not candidates:
        candidates = reference_hits[:MAX_CONCEPT_READS]

    # 3. peek before read; both stay within the token budget — peeks over large
    #    bundles are not free, and the budget is a hard cap, not a suggestion.
    read_docs: list[dict[str, Any]] = []
    budget_exhausted = False
    for path in candidates:
        if traced.spent_tokens >= budget_tokens:
            budget_exhausted = True
            break
        traced.call("peek", path=path)
    for path in candidates:
        if traced.spent_tokens >= budget_tokens:
            budget_exhausted = True
            break
        doc = traced.call("read", path=path)
        doc["_terms"] = terms
        doc["_via"] = None
        read_docs.append(doc)

    # 4. follow concept→concept links with bounded fan-out (≤4). A hop is
    #    justified by its anchor text, so the linked document's *excerpt* is
    #    chosen with the question terms plus the anchor's salient terms — the
    #    hop's payoff usually answers in different vocabulary than the question
    #    (that is why it needed a link to be found).
    followed = 0
    seen_paths = {str(doc["path"]) for doc in read_docs}
    for doc in list(read_docs):
        if followed >= MAX_LINK_FANOUT or traced.spent_tokens >= budget_tokens:
            break
        parent = str(doc["path"])
        if not bundle_mod.is_concept(parent):
            continue
        anchors = {
            str(link["resolved"]): str(link["text"])
            for link in doc["links"]
            if isinstance(link, dict) and link.get("resolved")
        }
        edges = traced.call("links", path=parent)
        for target in list(edges["outbound"]):
            if followed >= MAX_LINK_FANOUT:
                break
            if traced.spent_tokens >= budget_tokens:
                budget_exhausted = True
                break
            if target in seen_paths or not bundle_mod.is_concept(target):
                continue
            seen_paths.add(target)
            linked = traced.call("read", path=target)
            linked["_terms"] = terms | set(question_terms(anchors.get(target, "")))
            linked["_via"] = parent
            read_docs.append(linked)
            followed += 1

    # 5. deterministic evidence selection: every read document ranks its
    #    matching prose paragraphs (_ranked_excerpts); each document's best
    #    excerpt claims a slot first — navigation found that document for a
    #    reason, and a multi-hop answer must keep both ends — then spare slots
    #    fill with the remaining paragraphs in rank order, so one concept's
    #    sections can complete an answer without crowding out another document
    #    (RAG's top-k can likewise take several chunks from one document).
    #    Reference snapshots of an already-cited concept are folded away.
    #    Anchor terms may *choose* a hop's excerpts (its payoff usually answers
    #    in different vocabulary), but ranking counts question-term hits alone,
    #    with the stable sort keeping navigation order (search rank, then hop
    #    discovery, then document order) as the tie-break. Ranking on
    #    anchor-augmented scores would let a single-salient-term question
    #    ("What is A2K?") rank every hop above the seed that defines the term,
    #    displacing the very document search put first.
    primaries: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    for doc in read_docs:
        doc_terms = doc["_terms"] if isinstance(doc["_terms"], set) else terms
        frontmatter = doc["frontmatter"] if isinstance(doc["frontmatter"], dict) else {}
        description = frontmatter.get("description")
        ranked = _ranked_excerpts(
            str(doc["body"]),
            doc_terms,
            description=description if isinstance(description, str) else "",
        )
        title = frontmatter.get("title") or str(doc["path"])
        for position, (excerpt, _matched) in enumerate(ranked):
            entry = {
                "path": str(doc["path"]),
                "title": str(title),
                "excerpt": excerpt,
                "score": len(terms & tokenize(excerpt)),
                "frontmatter": frontmatter,
                "via": doc["_via"],
            }
            (primaries if position == 0 else extras).append(entry)
    primaries.sort(key=lambda e: -int(e["score"]))  # stable: ties keep navigation order
    extras.sort(key=lambda e: -int(e["score"]))
    cited_resources: set[str] = set()
    for entry in primaries + extras:
        for source in Frontmatter(data=entry["frontmatter"]).sources:
            resource = source.get("resource")
            if isinstance(resource, str):
                cited_resources.add(resource)
    evidence = [e for e in primaries + extras if e["path"] not in cited_resources][:MAX_EVIDENCE]

    citations: list[Citation] = []
    warnings: list[str] = []
    model_usage: dict[str, Any] | None = None
    if not evidence:
        abstained = True
        wanted = ", ".join(sorted(terms)) or "the question topic"
        if candidates:
            answer = (
                "Insufficient evidence in this bundle to answer. "
                f"The closest match is {candidates[0]}, but it does not address: {wanted}. "
                f"A concept covering {wanted} would resolve the gap."
            )
        else:
            answer = (
                "Insufficient evidence in this bundle to answer. "
                f"No concept mentions: {wanted}. "
                f"Adding a source about {wanted} would resolve the gap."
            )
    else:
        abstained = False
        answer = provider.complete(
            "answer-question",
            {
                "question": question,
                "evidence": [
                    {"path": e["path"], "title": e["title"], "excerpt": e["excerpt"]}
                    for e in evidence
                ],
            },
        )
        model_usage = getattr(provider, "last_usage", None)
        cited_paths: set[str] = set()
        for entry in evidence:
            path = str(entry["path"])
            if path in cited_paths:
                continue  # several excerpts of one document cite it once
            cited_paths.add(path)
            frontmatter = entry["frontmatter"]
            citations.append(_citation_for(path, frontmatter, entry["title"]))
            if bundle_mod.is_concept(path):
                warnings.extend(_concept_warnings(path, frontmatter, today))

    latency_ms = int((timer() - t0) * 1000)
    trace: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "question": question,
        "condition": condition,
        "model": provider.id,
        "revision_id": read_current_revision_id(explorer.root),
        "started_at": started_at,
        "latency_ms": latency_ms,
        "budget": {
            "limit_tokens": budget_tokens,
            "spent_tokens": traced.spent_tokens,
            "spent_chars": traced.spent_chars,
            "exhausted": budget_exhausted,
        },
        "policy": {
            "max_link_fanout": MAX_LINK_FANOUT,
            "max_concept_reads": MAX_CONCEPT_READS,
            "max_evidence": MAX_EVIDENCE,
            "excerpt_chars": EXCERPT_CHARS,
        },
        "model_usage": model_usage,
        "tools": traced.events,
        "paths_read": sorted({str(doc["path"]) for doc in read_docs}),
        "hops": [
            {"path": str(doc["path"]), "via": doc["_via"]}
            for doc in read_docs
            if doc["_via"] is not None
        ],
        "evidence_paths": [str(e["path"]) for e in evidence],
        "abstained": abstained,
    }

    return AskResult(
        question=question,
        answer=answer,
        abstained=abstained,
        citations=citations,
        warnings=warnings,
        trace=trace,
    )


def _answer_rag(
    *,
    bundle_dir: Path,
    question: str,
    provider: ModelProvider,
    embedder: EmbeddingProvider,
    budget_tokens: int,
    clock: Callable[[], str],
    timer: Callable[[], float],
) -> AskResult:
    """The vector top-k baseline.

    Retrieval is embedding-only over reference-snapshot chunks — no index-first
    navigation, no link following, and no concept trust metadata, which is
    exactly what the PD-vs-RAG comparison measures (RQ3: references carry no
    status/verification fields, so the warnings channel is structurally empty).
    """
    started_at = clock()
    t0 = timer()

    index = ensure_rag_index(bundle_dir, embedder)
    [query_vector] = embedder.embed([question])
    hits = retrieve(index, query_vector, MAX_EVIDENCE)

    # Equal budgets with PD: evidence is capped per-passage by construction
    # (CHUNK_CHARS == EXCERPT_CHARS) and total retrieved chars are budgeted.
    events: list[dict[str, Any]] = []
    spent_chars = len(question)
    selected: list[dict[str, Any]] = []
    budget_exhausted = False
    for hit in hits:
        if estimate_tokens(spent_chars + len(hit["text"])) > budget_tokens:
            budget_exhausted = True
            break
        spent_chars += len(hit["text"])
        selected.append(hit)
    events.append(
        {
            "tool": "vector_search",
            "args": {"k": MAX_EVIDENCE, "embedder": embedder.id, "chunks": len(index["chunks"])},
            "chars": spent_chars,
            "tokens": estimate_tokens(spent_chars),
        }
    )

    frontmatters: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for hit in selected:
        path = str(hit["path"])
        if path not in frontmatters:
            parsed = parse_document((bundle_dir / path).read_text(encoding="utf-8"))
            frontmatters[path] = parsed.frontmatter.data if parsed.frontmatter else {}
        title = frontmatters[path].get("title") or path
        evidence.append({"path": path, "title": str(title), "excerpt": str(hit["text"])})

    citations: list[Citation] = []
    model_usage: dict[str, Any] | None = None
    if not evidence:
        abstained = True
        wanted = ", ".join(sorted(set(question_terms(question)))) or "the question topic"
        answer = (
            "Insufficient evidence in this bundle to answer. "
            f"No indexed passage matches: {wanted}. "
            f"Adding a source about {wanted} would resolve the gap."
        )
    else:
        abstained = False
        answer = provider.complete(
            "answer-question",
            {
                "question": question,
                "evidence": [
                    {"path": e["path"], "title": e["title"], "excerpt": e["excerpt"]}
                    for e in evidence
                ],
            },
        )
        model_usage = getattr(provider, "last_usage", None)
        for path in dict.fromkeys(str(e["path"]) for e in evidence):
            frontmatter = frontmatters[path]
            title = str(frontmatter.get("title") or path)
            citations.append(_citation_for(path, frontmatter, title))

    latency_ms = int((timer() - t0) * 1000)
    trace: dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "question": question,
        "condition": "rag",
        "model": provider.id,
        "revision_id": index["revision_id"],
        "started_at": started_at,
        "latency_ms": latency_ms,
        "budget": {
            "limit_tokens": budget_tokens,
            "spent_tokens": estimate_tokens(spent_chars),
            "spent_chars": spent_chars,
            "exhausted": budget_exhausted,
        },
        "policy": {
            "k": MAX_EVIDENCE,
            "chunk_chars": CHUNK_CHARS,
            "embedder": embedder.id,
            "index_chunks": len(index["chunks"]),
        },
        "model_usage": model_usage,
        "tools": events,
        "paths_read": sorted({str(e["path"]) for e in evidence}),
        "hops": [],
        "evidence_paths": [str(e["path"]) for e in evidence],
        "abstained": abstained,
    }

    return AskResult(
        question=question,
        answer=answer,
        abstained=abstained,
        citations=citations,
        # References carry no status/stale/verified metadata, so RAG cannot
        # flag draft/deprecated/stale evidence — a measured property, not a bug.
        warnings=[],
        trace=trace,
    )


def write_trace(bundle_dir: Path, result: AskResult) -> Path:
    """Persist the trace as derived state under ``.oknoll/traces/``."""
    traces = bundle_dir / TRACES_DIR
    traces.mkdir(parents=True, exist_ok=True)
    stamp = str(result.trace.get("started_at", "")).replace(":", "").replace("-", "")
    digest = sha256_hex(result.question)[:8]
    base = f"ask-{stamp}-{digest}"
    path = traces / f"{base}.json"
    counter = 2
    while path.exists():
        path = traces / f"{base}-{counter}.json"
        counter += 1
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path

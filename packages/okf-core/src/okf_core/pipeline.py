"""Pipeline stages: acquire → normalize → plan → generate → link → lint → index →
publish. The CLI and the cloud pipeline job run this identical code.

Connectors are duck-typed through structural protocols so okf-core never
depends on the connectors package: connectors acquire and normalize only, and
this module owns everything that writes OKF.

Determinism: given the same sources, connector/prompt/model/
generator versions, and the build cache under ``.oknoll/cache/``, a rebuild
produces byte-identical content and therefore the same revision id.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from okf_core import bundle as bundle_mod
from okf_core import indexing
from okf_core._version import __version__ as core_version
from okf_core.cache import BuildCache
from okf_core.canonical import Block, CanonicalDoc, SourceRef, sha256_hex
from okf_core.findings import LintReport
from okf_core.frontmatter import Frontmatter, ParsedDocument, write_document
from okf_core.lint import lint_bundle
from okf_core.provider import (
    DEFAULT_GENERATION_VERSION,
    GENERATOR_VERSION,
    ModelProvider,
    generation_cache_key,
    generation_timestamp_key,
)
from okf_core.revision import (
    compare_trees,
    compute_revision_id,
    log_with_entry,
    parent_log_text,
    publish_revision,
    read_current_revision_id,
    revision_dir,
    write_manifest,
)

CACHE_PATH = f"{bundle_mod.DERIVED_STATE_DIR}/cache/build-cache.json"
RUN_LOGS_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/run-logs"
STAGE_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/tmp/stage"
DIFF_STAGE_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/tmp/diff"

STAGES: tuple[str, ...] = (
    "acquire",
    "normalize",
    "plan",
    "generate",
    "link",
    "lint",
    "index",
    "publish",
)


class PipelineError(RuntimeError):
    pass


class RawItemLike(Protocol):
    """The provenance surface the pipeline needs from an acquired item.

    Read-only properties so frozen dataclasses (like connectors' RawItem) conform.
    """

    @property
    def uri(self) -> str: ...

    @property
    def media_type(self) -> str: ...

    @property
    def source_hash(self) -> str: ...

    @property
    def retrieved_at(self) -> str: ...


class ConnectorLike(Protocol):
    """Structural connector protocol; implemented by oknoll_connectors."""

    id: str
    version: str

    def acquire(self, source: SourceRef, policy: Any) -> Iterable[RawItemLike]: ...

    # `item` is Any (not RawItemLike): implementations take their own concrete
    # item type, which parameter contravariance would otherwise reject.
    def normalize(self, item: Any) -> Iterable[CanonicalDoc]: ...


@dataclass(frozen=True, slots=True)
class PipelineSource:
    source: SourceRef
    connector: ConnectorLike
    policy: Any = None


@dataclass(slots=True)
class _SourceUnit:
    """One acquired item, its normalized docs, and its assigned bundle identity."""

    uri: str
    media_type: str
    source_hash: str
    retrieved_at: str
    connector_id: str
    connector_version: str
    docs: list[CanonicalDoc]
    source_id: str = ""
    ref_path: str = ""

    @property
    def title(self) -> str:
        return self.docs[0].title if self.docs else Path(self.uri).name


@dataclass(frozen=True, slots=True)
class ConceptPlan:
    doc: CanonicalDoc
    unit: _SourceUnit
    concept_path: str


@dataclass
class BuildOutcome:
    revision_id: str
    published: bool  # a new revision was published this run
    changed: bool  # content differs from the previous current revision
    lint_report: LintReport
    report: dict[str, Any]
    staged_dir: Path | None  # kept when the build did not publish


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "concept"


_MD_LINK_RE = re.compile(r"!?\[(?P<text>[^\]]*)\]\([^)]*\)")
_FOOTNOTE_TOKEN_RE = re.compile(r"\[\^[^\]]+\]:?")
# A concept-plan reply wrapped in a Markdown code fence still parses; anything
# else non-JSON falls back to one concept per document.
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```$", re.DOTALL)


def _strip_links(text: str) -> str:
    """Source text quoted inside a concept keeps its words, not its link targets —
    relative targets would dangle from the concept's directory.

    Stripping runs to a fixpoint: deleting one link must not splice the
    surrounding characters into a new one (`[a][](/x)(/y)` → `[a](/y)`), and any
    leftover `](` is broken apart so no quoted fragment parses as a live link.
    """
    while True:
        stripped = _MD_LINK_RE.sub(lambda m: m.group("text"), text)
        if stripped == text:
            break
        text = stripped
    return text.replace("](", "] (")


# Per-section digest bounds. Whole entries fit in one ask evidence excerpt
# (EXCERPT_CHARS) so the text that wins term scoring is exactly the text the
# model sees; the body budget keeps `read` sizes and the 25k-token ask budget
# honest even for very large sources.
DIGEST_MAX_LEVEL = 2
DIGEST_HEADING_CHARS = 200
DIGEST_ENTRY_CHARS = 500  # == okf_core.ask.EXCERPT_CHARS (contract-tested)
DIGEST_BODY_BUDGET = 10_000
DIGEST_LEAD_PARAGRAPHS = 3
_DIGEST_PROSE_KINDS = frozenset({"paragraph", "quote", "list"})

# Concept-plan bounds: the model only groups outline sections and names the
# groups — it never contributes body text, and a plan outside these bounds is
# discarded in favor of one concept per document.
PLAN_MAX_CONCEPTS = 12
PLAN_SNIPPET_CHARS = 200
PLAN_TITLE_CHARS = 120

# Cross-concept references per concept; capped like the explorer's link fan-out
# so one hub concept cannot dominate navigation.
RELATED_MAX_LINKS = 5

# Self-describing bundle bounds. Index entry lines carry one sentence so the
# root index stays a scannable router; the reference/bundle description prompts
# see bounded, link-stripped material only.
INDEX_LINE_CHARS = 200
REFERENCE_DESC_INPUT_CHARS = 3_000
REFERENCE_DESC_CHARS = 300
BUNDLE_DESC_MAX_CONCEPTS = 40
BUNDLE_DESC_PAYLOAD_CHARS = 6_000
BUNDLE_DESC_CHARS = 500

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s")


def _digest_text(text: str) -> str:
    # Footnote tokens go too: a bare marker would dangle (no definition follows
    # it into the digest) and a leading `[^id]:` could forge a definition that
    # shadows the generated provenance footnote.
    return " ".join(_FOOTNOTE_TOKEN_RE.sub("", _strip_links(text)).split())


def _index_line(text: str) -> str:
    """First sentence of a description, sanitized for a root-index bullet.

    Model descriptions render inside index bullets, so the text is link-stripped
    and whitespace-collapsed (``_digest_text``) — a hostile description can never
    smuggle a live link or newline into the index — then cut at the first
    sentence boundary and length-capped.
    """
    first = _SENTENCE_SPLIT_RE.split(_digest_text(text), maxsplit=1)[0]
    return first[:INDEX_LINE_CHARS].strip()


def _section_digest(doc: CanonicalDoc) -> list[str]:
    """Body sections summarizing the source: a short lead, then every section's
    heading with its opening prose.

    A question whose answer lives in a later section must still term-match the
    concept body, so each section contributes its heading *and* first prose block
    as one excerpt-sized unit. Spec-style documents open with metadata
    paragraphs, which under the old "first three paragraphs" rule were the only
    body content. The section level adapts to the document: a leading H1 is the
    document's own title, not a section, and a document written entirely in
    deeper headings is segmented at the shallowest level it actually uses.
    """
    blocks = list(doc.blocks)
    if blocks and blocks[0].kind == "heading" and (blocks[0].level or 1) == 1:
        blocks = blocks[1:]

    levels = [b.level or 1 for b in blocks if b.kind == "heading"]
    section_level = max(DIGEST_MAX_LEVEL, min(levels)) if levels else 0
    first_heading = next(
        (
            i
            for i, b in enumerate(blocks)
            if b.kind == "heading" and (b.level or 1) <= section_level
        ),
        len(blocks),
    )

    parts: list[str] = []
    lead = [
        text
        for b in blocks[:first_heading]
        if b.kind == "paragraph" and (text := _digest_text(b.text)[:DIGEST_ENTRY_CHARS])
    ][:DIGEST_LEAD_PARAGRAPHS]
    if lead:
        parts.append("\n\n".join(lead))

    entries: list[str] = []
    total = sum(len(p) + 2 for p in lead)
    heading: str | None = None
    section_blocks: list[Block] = []

    def flush() -> None:
        nonlocal total
        if heading is None:
            return
        title = _digest_text(heading)[:DIGEST_HEADING_CHARS]
        prefix = f"**{title}** — "
        prose = next((b.text for b in section_blocks if b.kind in _DIGEST_PROSE_KINDS), "")
        content = _digest_text(prose)[: DIGEST_ENTRY_CHARS - len(prefix)]
        entry = prefix + content if content else f"**{title}**"
        if total + len(entry) > DIGEST_BODY_BUDGET:
            entry = f"**{title}**"  # over budget: keep the outline, drop the prose
            if total + len(entry) > DIGEST_BODY_BUDGET:
                return
        entries.append(entry)
        total += len(entry) + 2

    for block in blocks[first_heading:]:
        if block.kind == "heading" and (block.level or 1) <= section_level:
            flush()
            heading, section_blocks = block.text, []
        else:
            section_blocks.append(block)
    flush()

    if entries:
        parts.append("# Sections\n\n" + "\n\n".join(entries))
    return parts


# -- stages ----------------------------------------------------------------


def _acquire_and_normalize(
    sources: Sequence[PipelineSource], cache: BuildCache
) -> list[_SourceUnit]:
    units: list[_SourceUnit] = []
    seen: set[str] = set()
    for pipeline_source in sources:
        connector = pipeline_source.connector
        for item in connector.acquire(pipeline_source.source, pipeline_source.policy):
            if item.uri in seen:
                continue
            seen.add(item.uri)
            stable_at = cache.acquired_at(item.source_hash, item.retrieved_at)
            docs = [replace(doc, retrieved_at=stable_at) for doc in connector.normalize(item)]
            units.append(
                _SourceUnit(
                    uri=item.uri,
                    media_type=item.media_type,
                    source_hash=item.source_hash,
                    retrieved_at=stable_at,
                    connector_id=connector.id,
                    connector_version=connector.version,
                    docs=docs,
                )
            )
    return units


def _split_sections(doc: CanonicalDoc) -> tuple[list[Block], list[tuple[str, list[Block]]]]:
    """Partition blocks at the digest section level: (preamble, [(heading, blocks)]).

    Mirrors ``_section_digest``'s adaptive level so the planner decides over the
    same boundaries the digest renders. A leading H1 is the document's own title
    and stays in the preamble.
    """
    blocks = list(doc.blocks)
    preamble: list[Block] = []
    if blocks and blocks[0].kind == "heading" and (blocks[0].level or 1) == 1:
        preamble.append(blocks[0])
        blocks = blocks[1:]

    levels = [b.level or 1 for b in blocks if b.kind == "heading"]
    section_level = max(DIGEST_MAX_LEVEL, min(levels)) if levels else 0

    sections: list[tuple[str, list[Block]]] = []
    current: list[Block] | None = None
    for block in blocks:
        if block.kind == "heading" and (block.level or 1) <= section_level:
            current = [block]
            sections.append((block.text, current))
        elif current is None:
            preamble.append(block)
        else:
            current.append(block)
    return preamble, sections


def _validate_plan(concepts: Any, section_count: int) -> list[dict[str, Any]] | None:
    """Normalize a model plan; ``[]`` means "keep whole", ``None`` means unusable.

    A usable plan partitions the section indexes exactly — no gaps, no
    duplicates — within the concept-count bound; titles are link-stripped and
    length-capped so planner output can never smuggle live Markdown into the
    bundle.
    """
    if not isinstance(concepts, list):
        return None
    if not concepts:
        return []
    if len(concepts) > PLAN_MAX_CONCEPTS:
        return None
    plan: list[dict[str, Any]] = []
    claimed: set[int] = set()
    for entry in concepts:
        if not isinstance(entry, dict):
            return None
        title = _digest_text(str(entry.get("title", "")))[:PLAN_TITLE_CHARS].strip()
        raw_sections = entry.get("sections")
        if not title or not isinstance(raw_sections, list) or not raw_sections:
            return None
        indexes: list[int] = []
        for value in raw_sections:
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            indexes.append(value)
        if indexes != sorted(indexes) or len(set(indexes)) != len(indexes):
            return None
        if claimed & set(indexes):
            return None
        claimed.update(indexes)
        plan.append({"title": title, "sections": indexes})
    if claimed != set(range(section_count)):
        return None
    return plan


def _planned_docs(
    doc: CanonicalDoc,
    provider: ModelProvider,
    cache: BuildCache,
    generation_version: str,
) -> list[CanonicalDoc]:
    """Bounded model split of one document into concept slices.

    The decision — including the fallback after unusable output — is cached by
    content hash so rebuilds replay it byte-for-byte and ``diff --check`` stays
    green behind a nondeterministic model. The model sees only link-stripped
    outline text and contributes only boundaries and titles, never body text.
    """
    preamble, sections = _split_sections(doc)
    if len(sections) < 2:
        return [doc]

    key = generation_cache_key(
        content_hash=doc.content_hash(),
        prompt_id="concept-plan",
        provider_id=provider.id,
        generation_version=generation_version,
    )
    cached = cache.generated(key)
    if cached is None:
        payload = {
            "title": doc.title,
            "sections": [
                {
                    "index": i,
                    "heading": _digest_text(heading)[:DIGEST_HEADING_CHARS],
                    "snippet": _digest_text(
                        next((b.text for b in blocks if b.kind in _DIGEST_PROSE_KINDS), "")
                    )[:PLAN_SNIPPET_CHARS],
                }
                for i, (heading, blocks) in enumerate(sections)
            ],
        }
        raw = _complete(provider, "concept-plan", payload, doc.title).strip()
        fenced = _FENCE_RE.match(raw)
        try:
            data = json.loads(fenced.group(1) if fenced else raw)
        except json.JSONDecodeError:
            data = None
        concepts = data.get("concepts") if isinstance(data, dict) else None
        cached = {
            "concepts": _validate_plan(concepts, len(sections)),
            "model": _served_model(provider),
        }
        cache.store_generated(key, cached)

    # Re-validate on the way out: the cache is derived state and may be foreign.
    concepts = cached.get("concepts") if isinstance(cached, dict) else None
    plan = _validate_plan(concepts, len(sections))
    if not plan:
        return [doc]

    slices: list[CanonicalDoc] = []
    for entry in plan:
        slice_blocks: list[Block] = []
        if 0 in entry["sections"]:
            slice_blocks.extend(preamble)
        for index in entry["sections"]:
            slice_blocks.extend(sections[index][1])
        slices.append(replace(doc, title=entry["title"], blocks=tuple(slice_blocks)))
    return slices


def _plan(
    units: list[_SourceUnit],
    provider: ModelProvider,
    cache: BuildCache,
    generation_version: str,
) -> list[ConceptPlan]:
    """Concept boundaries: bounded model split decisions over section outlines,
    falling back to one concept per document.

    The stub always answers "keep whole", so CI bundles are byte-identical to
    the pre-planner era; real providers may slice a document into several
    concepts, each carrying the same source provenance.
    """
    plans: list[ConceptPlan] = []
    used_paths: set[str] = set()
    for index, unit in enumerate(units, start=1):
        unit.source_id = f"source-{index:03d}"
        unit.ref_path = f"{bundle_mod.REFERENCES_DIR}/{unit.source_id}.md"
        for doc in unit.docs:
            for concept_doc in _planned_docs(doc, provider, cache, generation_version):
                base = _slugify(concept_doc.title)
                concept_path = f"concepts/{base}.md"
                counter = 2
                while concept_path in used_paths:
                    concept_path = f"concepts/{base}-{counter}.md"
                    counter += 1
                used_paths.add(concept_path)
                plans.append(ConceptPlan(doc=concept_doc, unit=unit, concept_path=concept_path))
    return plans


def _title_matches(terms: set[str], tokens: set[str]) -> bool:
    """All salient title terms must occur, tolerating one missing when the
    title has three or more — prose says "the wire protocol" where the title
    says "Wire Protocol Spec"."""
    if not terms:
        return False
    matched = len(terms & tokens)
    return matched == len(terms) or (len(terms) >= 3 and matched >= len(terms) - 1)


def _related_links(plans: list[ConceptPlan]) -> dict[str, list[ConceptPlan]]:
    """Cross-concept references from title-term matching: A links to B when B's
    salient title terms occur in A's text (see ``_title_matches``).

    Pure code over link-stripped source text — sorted and capped — so the link
    graph never depends on model output and viz/PD hops gain edges even under
    the stub.
    """
    token_sets: dict[str, set[str]] = {}
    term_sets: dict[str, set[str]] = {}
    for plan in plans:
        text = " ".join(_digest_text(block.text) for block in plan.doc.blocks)
        token_sets[plan.concept_path] = set(indexing.tokenize(text))
        term_sets[plan.concept_path] = set(indexing.question_terms(plan.doc.title))

    related: dict[str, list[ConceptPlan]] = {}
    for plan in plans:
        candidates = [
            other
            for other in plans
            if other.concept_path != plan.concept_path
            and _title_matches(term_sets[other.concept_path], token_sets[plan.concept_path])
        ]
        candidates.sort(key=lambda p: p.concept_path)
        related[plan.concept_path] = candidates[:RELATED_MAX_LINKS]
    return related


def render_blocks(blocks: Iterable[Block]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.kind == "heading":
            parts.append(f"{'#' * (block.level or 1)} {block.text}")
        elif block.kind == "code":
            parts.append(f"```{block.language or ''}\n{block.text}\n```")
        elif block.kind == "quote":
            parts.append("\n".join(f"> {line}" for line in block.text.split("\n")))
        else:
            parts.append(block.text)
    return "\n\n".join(part for part in parts if part.strip())


def _generate(
    stage: Path,
    units: list[_SourceUnit],
    plans: list[ConceptPlan],
    provider: ModelProvider,
    cache: BuildCache,
    clock: Callable[[], str],
    generation_version: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Write references and concepts; return their descriptions keyed by bundle
    path (``concepts_by_path, references_by_path``) so the link stage can build
    a descriptive index without re-asking the provider."""
    related = _related_links(plans)
    reference_descriptions: dict[str, str] = {}
    for unit in units:
        reference_descriptions[unit.ref_path] = _write_reference_snapshot(
            stage, unit, provider, cache, generation_version
        )
    concept_descriptions: dict[str, str] = {}
    for plan in plans:
        concept_descriptions[plan.concept_path] = _write_concept(
            stage, plan, provider, cache, clock, generation_version, related[plan.concept_path]
        )
    return concept_descriptions, reference_descriptions


def _reference_description(
    unit: _SourceUnit,
    provider: ModelProvider,
    cache: BuildCache,
    generation_version: str,
) -> str:
    """One-sentence description of an acquired source, cached like every other
    model-generated field; unusable output falls back deterministically and the
    fallback decision is cached."""
    key = generation_cache_key(
        content_hash=sha256_hex("\x00".join(doc.content_hash() for doc in unit.docs)),
        prompt_id="reference-description",
        provider_id=provider.id,
        generation_version=generation_version,
    )
    cached = cache.generated(key)
    if cached is None:
        outline = "\n\n".join(part for doc in unit.docs for part in _section_digest(doc))[
            :REFERENCE_DESC_INPUT_CHARS
        ]
        payload = {"title": unit.title, "outline": outline}
        raw = _complete(provider, "reference-description", payload, unit.title)
        cached = {
            "description": _digest_text(raw)[:REFERENCE_DESC_CHARS].strip(),
            "model": _served_model(provider),
        }
        cache.store_generated(key, cached)
    # Re-validate on the way out: the cache is derived state and may be foreign.
    description = str(cached.get("description", "")) if isinstance(cached, dict) else ""
    description = _digest_text(description)[:REFERENCE_DESC_CHARS].strip()
    return description or f"Acquired source snapshot: {unit.title}."


def _write_reference_snapshot(
    stage: Path,
    unit: _SourceUnit,
    provider: ModelProvider,
    cache: BuildCache,
    generation_version: str,
) -> str:
    description = _reference_description(unit, provider, cache, generation_version)
    frontmatter = Frontmatter(
        data={
            "title": unit.title,
            "description": description,
            "openknoll_source": {
                "connector": unit.connector_id,
                "connector_version": unit.connector_version,
                "uri": unit.uri,
                "media_type": unit.media_type,
                "source_hash": unit.source_hash,
                "retrieved_at": unit.retrieved_at,
            },
        }
    )
    body = "\n\n".join(rendered for doc in unit.docs if (rendered := render_blocks(doc.blocks)))
    _write_file(stage / unit.ref_path, write_document(ParsedDocument(frontmatter, body)))
    return description


def _complete(provider: ModelProvider, prompt_id: str, payload: dict[str, Any], title: str) -> str:
    """One provider call, with the failing document named in the error — a
    refusal or truncation on source 7 of 40 is otherwise undebuggable."""
    try:
        return provider.complete(prompt_id, payload)
    except Exception as exc:
        raise PipelineError(f"{prompt_id} failed for {title!r}: {exc}") from exc


def _served_model(provider: ModelProvider) -> str:
    """The model that actually produced the last completion.

    Providers with a refusal-fallback strategy may serve a request with a
    different model than the one addressed; recording it beside each cached
    generation keeps provenance honest while the cache *key* stays the
    requested provider id (the request's identity).
    """
    return str(getattr(provider, "served_model_id", provider.id))


def _generated_fields(
    doc: CanonicalDoc,
    provider: ModelProvider,
    cache: BuildCache,
    clock: Callable[[], str],
    generation_version: str,
) -> dict[str, Any]:
    key = generation_cache_key(
        content_hash=doc.content_hash(),
        prompt_id="concept-description",
        provider_id=provider.id,
        generation_version=generation_version,
    )
    cached = cache.generated(key)
    if cached is not None:
        return cached
    excerpt = _strip_links(next((b.text for b in doc.blocks if b.kind == "paragraph"), ""))[:400]
    payload = {"title": doc.title, "excerpt": excerpt, "content_hash": doc.content_hash()}
    description = _complete(provider, "concept-description", payload, doc.title)
    model = _served_model(provider)
    # generated_at is keyed without generation_version and fingerprinted by the
    # output: a bump that reproduces identical output keeps its first-produced
    # timestamp (the revision must not change on timestamp noise alone), while
    # changed output gets a fresh, honest one.
    timestamp_key = generation_timestamp_key(
        content_hash=doc.content_hash(),
        prompt_id="concept-description",
        provider_id=provider.id,
    )
    fields: dict[str, Any] = {
        "description": description,
        "generated_at": cache.stable_generated_at(
            timestamp_key, sha256_hex("\x00".join((description, model))), clock()
        ),
        "model": model,
    }
    cache.store_generated(key, fields)
    return fields


def _write_concept(
    stage: Path,
    plan: ConceptPlan,
    provider: ModelProvider,
    cache: BuildCache,
    clock: Callable[[], str],
    generation_version: str,
    related: list[ConceptPlan],
) -> str:
    doc, unit = plan.doc, plan.unit
    generated = _generated_fields(doc, provider, cache, clock, generation_version)
    # The description is model output rendered into the bundle body and
    # frontmatter: link-strip and collapse it like every other model field so
    # it can never smuggle a live link past lint (the cache keeps the raw
    # reply; sanitization is deterministic on the way out).
    description = _digest_text(str(generated["description"]))

    frontmatter = Frontmatter(
        data={
            "type": "Concept",
            "title": doc.title,
            "description": description,
            "status": "draft",
            "generated": {"by": f"oknoll/{core_version}", "at": generated["generated_at"]},
            "sources": [{"id": unit.source_id, "resource": unit.ref_path, "title": unit.title}],
        }
    )

    sections = [f"# Summary\n\n{description}[^{unit.source_id}]"]
    sections.extend(_section_digest(doc))
    if related:
        links = "\n".join(f"- [{other.doc.title}](/{other.concept_path})" for other in related)
        sections.append(f"# Related\n\n{links}")
    sections.append(f"# Sources\n\n- [{unit.title}](/{unit.ref_path})")
    sections.append(f"[^{unit.source_id}]: {unit.title} ({unit.uri})")

    body = "\n\n".join(sections)
    _write_file(stage / plan.concept_path, write_document(ParsedDocument(frontmatter, body)))
    return description


def _bundle_description(
    project_name: str,
    plans: list[ConceptPlan],
    concept_descriptions: dict[str, str],
    provider: ModelProvider,
    cache: BuildCache,
    generation_version: str,
) -> str:
    """Bundle-level description for the root index, from concept/reference
    titles and descriptions only.

    The cache content hash is the hash of the payload itself — a pure function
    of the ordered per-concept content — so rebuilds replay the decision and
    ``diff --check`` stays green. Unusable output falls back deterministically
    and the fallback decision is cached.
    """
    if not plans:
        return f"OKF bundle for {project_name}."

    ordered = sorted(plans, key=lambda p: p.concept_path)
    concepts: list[dict[str, str]] = [
        {
            "title": plan.doc.title,
            "description": _index_line(concept_descriptions.get(plan.concept_path, "")),
        }
        for plan in ordered[:BUNDLE_DESC_MAX_CONCEPTS]
    ]
    seen_refs: dict[str, str] = {}
    for plan in ordered:
        seen_refs.setdefault(plan.unit.ref_path, plan.unit.title)
    references = [title for _, title in sorted(seen_refs.items())]
    payload = {"name": project_name, "concepts": concepts, "references": references}
    if len(json.dumps(payload)) > BUNDLE_DESC_PAYLOAD_CHARS:
        # Over budget: drop descriptions first, then trim entries, Google-style.
        concepts = [{"title": entry["title"]} for entry in concepts]
        payload = {"name": project_name, "concepts": concepts, "references": references}
        while concepts and len(json.dumps(payload)) > BUNDLE_DESC_PAYLOAD_CHARS:
            concepts.pop()
            payload = {"name": project_name, "concepts": concepts, "references": references}

    key = generation_cache_key(
        content_hash=sha256_hex(json.dumps(payload, sort_keys=True)),
        prompt_id="bundle-description",
        provider_id=provider.id,
        generation_version=generation_version,
    )
    cached = cache.generated(key)
    if cached is None:
        raw = _complete(provider, "bundle-description", payload, project_name)
        cached = {
            "description": _digest_text(raw)[:BUNDLE_DESC_CHARS].strip(),
            "model": _served_model(provider),
        }
        cache.store_generated(key, cached)
    # Re-validate on the way out: the cache is derived state and may be foreign.
    description = str(cached.get("description", "")) if isinstance(cached, dict) else ""
    description = _digest_text(description)[:BUNDLE_DESC_CHARS].strip()
    if description:
        return description
    covered = ", ".join(plan.doc.title for plan in ordered[:5])
    return (
        f"Knowledge bundle for {project_name}: {len(plans)} concept(s) from "
        f"{len(seen_refs)} source(s), covering {covered}."
    )


def _index_entry(title: str, path: str, description: str) -> str:
    line = _index_line(description)
    link = f"- [{title}](/{path})"
    return f"{link} - {line}" if line else link


def _link(
    stage: Path,
    project_name: str,
    plans: list[ConceptPlan],
    concept_descriptions: dict[str, str],
    reference_descriptions: dict[str, str],
    bundle_description: str,
) -> None:
    """Generate the root index: bundle-root-absolute links, each entry carrying
    a one-sentence sanitized description (see ``_index_line``)."""
    frontmatter = Frontmatter(
        data={
            "okf_version": "0.2",
            "title": project_name,
            "description": bundle_description,
        }
    )
    lines = [f"# {project_name}"]
    if plans:
        concept_links = "\n".join(
            _index_entry(
                plan.doc.title,
                plan.concept_path,
                concept_descriptions.get(plan.concept_path, ""),
            )
            for plan in sorted(plans, key=lambda p: p.concept_path)
        )
        lines.append(f"## Concepts\n\n{concept_links}")
        seen_refs: dict[str, str] = {}
        for plan in plans:
            seen_refs.setdefault(plan.unit.ref_path, plan.unit.title)
        reference_links = "\n".join(
            _index_entry(title, ref_path, reference_descriptions.get(ref_path, ""))
            for ref_path, title in sorted(seen_refs.items())
        )
        lines.append(f"## References\n\n{reference_links}")
    else:
        lines.append("No concepts yet. Run `oknoll add SOURCE` and `oknoll build`.")
    body = "\n\n".join(lines)
    _write_file(stage / bundle_mod.INDEX_NAME, write_document(ParsedDocument(frontmatter, body)))


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# -- orchestration ---------------------------------------------------------


def build_revision(
    *,
    bundle_dir: Path,
    project_name: str,
    sources: Sequence[PipelineSource],
    provider: ModelProvider,
    stage_dir: Path | None = None,
    publish: bool = True,
    clock: Callable[[], str] | None = None,
    generation_version: str = DEFAULT_GENERATION_VERSION,
) -> BuildOutcome:
    """Run the full pipeline; publish a new immutable revision when content changed.

    With ``publish=False`` the staged tree is left in place (used by diff --check).
    ``generation_version`` is the user-facing regeneration knob: bumping it in
    project config invalidates every cached model generation for this bundle.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    clock = clock or _utc_now
    cache = BuildCache.load(bundle_dir / CACHE_PATH)

    stage = stage_dir if stage_dir is not None else bundle_dir / STAGE_DIR
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    try:
        units = _acquire_and_normalize(sources, cache)
        plans = _plan(units, provider, cache, generation_version)
        concept_descriptions, reference_descriptions = _generate(
            stage, units, plans, provider, cache, clock, generation_version
        )
        description = _bundle_description(
            project_name, plans, concept_descriptions, provider, cache, generation_version
        )
    except Exception:
        # A failed build (e.g. one refused model call) must not discard the
        # model decisions that already succeeded: the cache is content-keyed,
        # so persisting it is always safe and makes a retry incremental.
        cache.save()
        raise
    _link(stage, project_name, plans, concept_descriptions, reference_descriptions, description)

    revision_id = compute_revision_id(stage)
    current = read_current_revision_id(bundle_dir)
    changed = revision_id != current
    summary = f"{len(plans)} concept(s) from {len(units)} source file(s)"
    if changed:
        log_text = log_with_entry(parent_log_text(bundle_dir), revision_id, summary)
    else:
        log_text = parent_log_text(bundle_dir)
    _write_file(stage / bundle_mod.LOG_NAME, log_text)
    write_manifest(stage)

    lint_report = lint_bundle(stage)
    graph = indexing.build_link_graph(stage)

    published = False
    staged_dir: Path | None = stage
    if publish and lint_report.passed():
        # Index stage: persist the FTS + graph index as derived
        # state keyed by revision id, so the explorer finds it ready-built.
        indexing.write_index(stage, indexing.index_dir_for(bundle_dir, revision_id))
        publish_revision(bundle_dir, stage, revision_id)
        published = changed
        cache.save()
        shutil.rmtree(stage, ignore_errors=True)
        staged_dir = None

    report: dict[str, Any] = {
        "revision_id": revision_id,
        "published": published,
        "changed": changed,
        "project": project_name,
        "provider": provider.id,
        "generator_version": GENERATOR_VERSION,
        "generation_version": generation_version,
        "okf_core_version": core_version,
        "stages": list(STAGES),
        "counts": {
            "source_files": len(units),
            "documents": sum(len(u.docs) for u in units),
            "concepts": len(plans),
        },
        "cache": {"hits": cache.hits, "misses": cache.misses},
        "lint": lint_report.to_dict()["summary"],
        "graph": graph,
    }
    if publish and lint_report.passed():
        run_log = bundle_dir / RUN_LOGS_DIR / f"{revision_id}.json"
        _write_file(run_log.parent / run_log.name, json.dumps(report, indent=2) + "\n")

    return BuildOutcome(
        revision_id=revision_id,
        published=published,
        changed=changed,
        lint_report=lint_report,
        report=report,
        staged_dir=staged_dir,
    )


def check_reproducibility(
    *,
    bundle_dir: Path,
    project_name: str,
    sources: Sequence[PipelineSource],
    provider: ModelProvider,
    clock: Callable[[], str] | None = None,
    generation_version: str = DEFAULT_GENERATION_VERSION,
) -> tuple[str, list[tuple[str, str]]]:
    """Rebuild to a temp stage and byte-compare against the current revision.

    Returns (current_revision_id, drift) where drift is a sorted list of
    (path, kind) pairs; an empty list means the build is reproducible.
    """
    current = read_current_revision_id(bundle_dir)
    if current is None:
        raise PipelineError("no published revision — run `oknoll build` first")

    stage = bundle_dir / DIFF_STAGE_DIR
    outcome = build_revision(
        bundle_dir=bundle_dir,
        project_name=project_name,
        sources=sources,
        provider=provider,
        stage_dir=stage,
        publish=False,
        clock=clock,
        generation_version=generation_version,
    )
    assert outcome.staged_dir is not None
    drift = compare_trees(revision_dir(bundle_dir, current), outcome.staged_dir)
    shutil.rmtree(stage, ignore_errors=True)
    return current, drift

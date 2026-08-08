"""Pipeline stages: acquire → normalize → plan → generate → link → lint → index →
publish (design §5.3). The CLI and the cloud pipeline job run this identical code.

Connectors are duck-typed through structural protocols so okf-core never
depends on the connectors package: connectors acquire and normalize only, and
this module owns everything that writes OKF.

Determinism (design §5.4): given the same sources, connector/prompt/model/
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
from okf_core.canonical import Block, CanonicalDoc, SourceRef
from okf_core.findings import LintReport
from okf_core.frontmatter import Frontmatter, ParsedDocument, write_document
from okf_core.lint import lint_bundle
from okf_core.provider import GENERATOR_VERSION, ModelProvider, generation_cache_key
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
    """Structural connector protocol (design §6.2); implemented by oknoll_connectors."""

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


def _digest_text(text: str) -> str:
    # Footnote tokens go too: a bare marker would dangle (no definition follows
    # it into the digest) and a leading `[^id]:` could forge a definition that
    # shadows the generated provenance footnote.
    return " ".join(_FOOTNOTE_TOKEN_RE.sub("", _strip_links(text)).split())


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


def _plan(units: list[_SourceUnit]) -> list[ConceptPlan]:
    """Deterministic concept boundaries: one concept per document (Phase 2).

    A model may only make bounded split/merge decisions here; the stub makes none.
    """
    plans: list[ConceptPlan] = []
    used_paths: set[str] = set()
    for index, unit in enumerate(units, start=1):
        unit.source_id = f"source-{index:03d}"
        unit.ref_path = f"{bundle_mod.REFERENCES_DIR}/{unit.source_id}.md"
        for doc in unit.docs:
            base = _slugify(doc.title)
            concept_path = f"concepts/{base}.md"
            counter = 2
            while concept_path in used_paths:
                concept_path = f"concepts/{base}-{counter}.md"
                counter += 1
            used_paths.add(concept_path)
            plans.append(ConceptPlan(doc=doc, unit=unit, concept_path=concept_path))
    return plans


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
) -> None:
    for unit in units:
        _write_reference_snapshot(stage, unit)
    for plan in plans:
        _write_concept(stage, plan, provider, cache, clock)


def _write_reference_snapshot(stage: Path, unit: _SourceUnit) -> None:
    frontmatter = Frontmatter(
        data={
            "title": unit.title,
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


def _generated_fields(
    doc: CanonicalDoc,
    provider: ModelProvider,
    cache: BuildCache,
    clock: Callable[[], str],
) -> dict[str, Any]:
    key = generation_cache_key(
        content_hash=doc.content_hash(),
        prompt_id="concept-description",
        provider_id=provider.id,
    )
    cached = cache.generated(key)
    if cached is not None:
        return cached
    excerpt = _strip_links(next((b.text for b in doc.blocks if b.kind == "paragraph"), ""))[:400]
    fields: dict[str, Any] = {
        "description": provider.complete(
            "concept-description",
            {"title": doc.title, "excerpt": excerpt, "content_hash": doc.content_hash()},
        ),
        "generated_at": clock(),
    }
    cache.store_generated(key, fields)
    return fields


def _write_concept(
    stage: Path,
    plan: ConceptPlan,
    provider: ModelProvider,
    cache: BuildCache,
    clock: Callable[[], str],
) -> None:
    doc, unit = plan.doc, plan.unit
    generated = _generated_fields(doc, provider, cache, clock)
    description = str(generated["description"])

    frontmatter = Frontmatter(
        data={
            "type": "Reference",
            "title": doc.title,
            "description": description,
            "status": "draft",
            "generated": {"by": f"oknoll/{core_version}", "at": generated["generated_at"]},
            "sources": [{"id": unit.source_id, "resource": unit.ref_path, "title": unit.title}],
        }
    )

    sections = [f"# Summary\n\n{description}[^{unit.source_id}]"]
    sections.extend(_section_digest(doc))
    sections.append(f"# Sources\n\n- [{unit.title}](/{unit.ref_path})")
    sections.append(f"[^{unit.source_id}]: {unit.title} ({unit.uri})")

    body = "\n\n".join(sections)
    _write_file(stage / plan.concept_path, write_document(ParsedDocument(frontmatter, body)))


def _link(stage: Path, project_name: str, plans: list[ConceptPlan]) -> None:
    """Generate the root index with bundle-root-absolute links (design §4.3)."""
    frontmatter = Frontmatter(
        data={
            "okf_version": "0.2",
            "title": project_name,
            "description": f"OKF bundle for {project_name}.",
        }
    )
    lines = [f"# {project_name}"]
    if plans:
        concept_links = "\n".join(
            f"- [{plan.doc.title}](/{plan.concept_path})"
            for plan in sorted(plans, key=lambda p: p.concept_path)
        )
        lines.append(f"## Concepts\n\n{concept_links}")
        seen_refs: dict[str, str] = {}
        for plan in plans:
            seen_refs.setdefault(plan.unit.ref_path, plan.unit.title)
        reference_links = "\n".join(
            f"- [{title}](/{ref_path})" for ref_path, title in sorted(seen_refs.items())
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
) -> BuildOutcome:
    """Run the full pipeline; publish a new immutable revision when content changed.

    With ``publish=False`` the staged tree is left in place (used by diff --check).
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    clock = clock or _utc_now
    cache = BuildCache.load(bundle_dir / CACHE_PATH)

    stage = stage_dir if stage_dir is not None else bundle_dir / STAGE_DIR
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    units = _acquire_and_normalize(sources, cache)
    plans = _plan(units)
    _generate(stage, units, plans, provider, cache, clock)
    _link(stage, project_name, plans)

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
        # Index stage (§5.3 stage 7): persist the FTS + graph index as derived
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
    )
    assert outcome.staged_dir is not None
    drift = compare_trees(revision_dir(bundle_dir, current), outcome.staged_dir)
    shutil.rmtree(stage, ignore_errors=True)
    return current, drift

"""Prompt templates for the okf-core prompt ids, rendered for real providers.

The deterministic stub embeds its own "prompt"; real providers share these
templates so Anthropic and Ollama answer from identical instructions. Changing
any template text changes model output for the same input, so it must be paired
with a bump of the matching entry in ``okf_core.provider.PROMPT_VERSIONS`` —
that is what keeps stale generations out of the build cache.
"""

from __future__ import annotations

from typing import Any

_CONCEPT_SYSTEM = (
    "You write short grounded descriptions for concepts in a knowledge bundle. "
    "You are given the concept's title and an outline of its sections. Use only "
    "the material given — never invent facts, names, or numbers that are not in "
    "it. Reply with one to three plain prose sentences and nothing else: no "
    "headings, no lists, no preamble. The first sentence must stand alone as a "
    "one-line summary of the whole concept, roughly 8 to 30 words; later "
    "sentences may add scope, caveats, or key details."
)

_PLAN_SYSTEM = (
    "You plan concept boundaries for a knowledge bundle. Given a document's "
    "section outline, group the sections into a small number of coherent "
    "concepts. Reply with JSON only — no prose, no code fences — of the form "
    '{"concepts": [{"title": "...", "sections": [0, 1]}, ...]}. Every section '
    "index must appear in exactly one concept. Prefer contiguous runs of "
    "sections and one to six concepts; titles are short noun phrases drawn "
    "from the outline's own vocabulary. If the document is best kept as a "
    'single concept, reply {"concepts": []}.'
)

_REFERENCE_SYSTEM = (
    "You write a one-sentence description of an acquired source document in a "
    "knowledge bundle. You are given its title and section outline. Use only "
    "the material given — never invent facts. Reply with exactly one plain "
    "prose sentence, roughly 8 to 30 words, stating what the document contains "
    "and what it is useful for. No headings, no lists, no preamble."
)

_BUNDLE_SYSTEM = (
    "You write the description for the root index of a knowledge bundle. You "
    "are given the bundle's name and the titles and descriptions of its "
    "concepts and acquired sources. Reply with one to two plain prose "
    "sentences, roughly 15 to 50 words, stating what the bundle covers and "
    "what an agent can find in it. Use only the material given — never invent "
    "topics. No headings, no lists, no preamble."
)

_ANSWER_SYSTEM = (
    "You answer questions using only the evidence passages provided. Every "
    "claim in your answer must be supported by one of the passages; cite the "
    "supporting passage's path in square brackets, e.g. [concepts/example.md]. "
    "If the evidence does not support an answer, say so plainly and name what "
    "is missing instead of guessing. Be concise: a short paragraph, no "
    "headings, no restating the question."
)


def render(prompt_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Return the (system, user) prompt pair for a named okf-core prompt id."""
    if prompt_id == "concept-description":
        title = str(payload.get("title", "")).strip() or "this concept"
        outline = str(payload.get("outline", "")).strip() or "(no outline)"
        user = f"Concept title: {title}\n\nSection outline:\n{outline}"
        return _CONCEPT_SYSTEM, user
    if prompt_id == "concept-plan":
        title = str(payload.get("title", "")).strip() or "(untitled document)"
        sections = payload.get("sections")
        items = sections if isinstance(sections, list) else []
        lines: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            heading = str(item.get("heading", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            lines.append(f"{index}. {heading} — {snippet}" if snippet else f"{index}. {heading}")
        outline = "\n".join(lines) if lines else "(no sections)"
        user = f"Document title: {title}\n\nSections:\n{outline}"
        return _PLAN_SYSTEM, user
    if prompt_id == "reference-description":
        title = str(payload.get("title", "")).strip() or "(untitled source)"
        outline = str(payload.get("outline", "")).strip() or "(no outline)"
        user = f"Source title: {title}\n\nSection outline:\n{outline}"
        return _REFERENCE_SYSTEM, user
    if prompt_id == "bundle-description":
        name = str(payload.get("name", "")).strip() or "(unnamed bundle)"
        concepts = payload.get("concepts")
        concept_items = concepts if isinstance(concepts, list) else []
        concept_lines: list[str] = []
        for item in concept_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            description = str(item.get("description", "")).strip()
            concept_lines.append(f"- {title}: {description}" if description else f"- {title}")
        references = payload.get("references")
        reference_items = references if isinstance(references, list) else []
        reference_lines = [f"- {str(item).strip()}" for item in reference_items]
        concept_text = "\n".join(concept_lines) if concept_lines else "(none)"
        reference_text = "\n".join(reference_lines) if reference_lines else "(none)"
        user = f"Bundle name: {name}\n\nConcepts:\n{concept_text}\n\nSources:\n{reference_text}"
        return _BUNDLE_SYSTEM, user
    if prompt_id == "answer-question":
        question = str(payload.get("question", "")).strip()
        evidence = payload.get("evidence")
        items = evidence if isinstance(evidence, list) else []
        blocks: list[str] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            title = str(item.get("title", "")).strip() or path
            excerpt = str(item.get("excerpt", "")).strip()
            blocks.append(f"Passage {index} — {title} [{path}]:\n{excerpt}")
        evidence_text = "\n\n".join(blocks) if blocks else "(no evidence passages)"
        user = f"Question: {question}\n\nEvidence:\n{evidence_text}"
        return _ANSWER_SYSTEM, user
    raise ValueError(f"provider has no prompt {prompt_id!r}")

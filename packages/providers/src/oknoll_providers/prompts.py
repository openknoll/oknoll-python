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
    "Use only the excerpt you are given — never invent facts, names, or numbers "
    "that are not in it. Reply with one to three plain prose sentences and "
    "nothing else: no headings, no lists, no preamble."
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
        excerpt = str(payload.get("excerpt", "")).strip()
        user = f"Concept title: {title}\n\nSource excerpt:\n{excerpt}"
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

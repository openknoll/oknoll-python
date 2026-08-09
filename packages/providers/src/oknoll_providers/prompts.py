"""Prompt templates for the two okf-core prompt ids, rendered for real providers.

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

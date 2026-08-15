"""Model provider interface with a deterministic stub for CI.

All model access in the pipeline goes through ``ModelProvider``. Model-generated
fields are cached by content hash + prompt id/version + model id + generator
version (see ``generation_cache_key``); the deterministic stub makes CI builds
fully reproducible without network access.
"""

from __future__ import annotations

from typing import Any, Protocol

from okf_core.canonical import sha256_hex

# Bumping either constant invalidates cached model-generated fields.
# 2: concept bodies carry a per-section digest instead of the first three
#    paragraphs — cached generated_at stamps must not pair with new bodies.
# 3: concepts carry a Related section and may be model-planned slices of a
#    source — cached plans and stamps from the one-concept-per-doc era must
#    not pair with the new bodies.
# 4: self-describing bundles — index entries carry per-entry descriptions,
#    reference snapshots and the root index carry model-generated
#    descriptions — v3 stamps and plans must not pair with the new bodies.
GENERATOR_VERSION = "4"
PROMPT_VERSIONS: dict[str, str] = {
    # 2: input is the concept's section outline (not the first paragraph) and
    #    the first sentence must stand alone as the index-line summary.
    "concept-description": "2",
    # 2: outline payload gained per-section subheadings and longer snippets;
    #    guidance retuned from "one to six" to topic-per-concept granularity.
    "concept-plan": "2",
    "reference-description": "1",
    "bundle-description": "1",
    "answer-question": "1",
    "chat-answer": "1",
}

# Default for the user-facing regeneration knob ([build].generation_version in
# oknoll.toml). Bumping it in a project is a deliberate, committed event that
# invalidates every cached model generation for that bundle — the escape hatch
# for "same model id, same prompts, but I want fresh output" (e.g. a provider
# alias whose underlying model improved).
DEFAULT_GENERATION_VERSION = "0"


class ModelProvider(Protocol):
    """A bounded text-completion interface; the pipeline never calls models directly."""

    id: str

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        """Return the completion for a named prompt over a JSON-safe payload."""
        ...


class StubModelProvider:
    """Deterministic CI provider: output is a pure function of the payload."""

    id = "stub"

    def complete(self, prompt_id: str, payload: dict[str, Any]) -> str:
        if prompt_id == "concept-description":
            title = str(payload.get("title", "")).strip() or "this concept"
            return f"Grounded notes on {title}, generated from the acquired source material."
        if prompt_id == "concept-plan":
            # The stub never splits: an empty concept list means "keep the
            # document as one concept", the pre-planner behavior.
            return '{"concepts": []}'
        if prompt_id == "reference-description":
            title = str(payload.get("title", "")).strip() or "this source"
            return f"Acquired source snapshot of {title}."
        if prompt_id == "bundle-description":
            name = str(payload.get("name", "")).strip() or "this bundle"
            concepts = payload.get("concepts")
            count = len(concepts) if isinstance(concepts, list) else 0
            return (
                f"Knowledge bundle for {name} covering {count} concept(s) "
                "built from acquired sources."
            )
        if prompt_id in ("answer-question", "chat-answer"):
            evidence = payload.get("evidence")
            items = evidence if isinstance(evidence, list) else []
            if prompt_id == "chat-answer":
                history = payload.get("history")
                prior = len(history) if isinstance(history, list) else 0
                lines = [f"Based on {len(items)} passage(s) and {prior} prior turn(s):"]
            else:
                lines = [f"Based on {len(items)} passage(s) retrieved from the bundle:"]
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip() or str(item.get("path", ""))
                excerpt = str(item.get("excerpt", "")).strip()
                lines.append(f"- {title}: {excerpt} [{item.get('path', '')}]")
            return "\n".join(lines)
        raise ValueError(f"stub provider has no prompt {prompt_id!r}")


def generation_cache_key(
    *,
    content_hash: str,
    prompt_id: str,
    provider_id: str,
    prompt_version: str | None = None,
    generator_version: str = GENERATOR_VERSION,
    generation_version: str = DEFAULT_GENERATION_VERSION,
) -> str:
    """Stable cache key for one model-generated field."""
    version = prompt_version if prompt_version is not None else PROMPT_VERSIONS[prompt_id]
    return sha256_hex(
        "\x00".join(
            (content_hash, prompt_id, version, provider_id, generator_version, generation_version)
        )
    )


def generation_timestamp_key(
    *,
    content_hash: str,
    prompt_id: str,
    provider_id: str,
    prompt_version: str | None = None,
    generator_version: str = GENERATOR_VERSION,
) -> str:
    """Identity under which a generation's first-produced timestamp is kept.

    Everything in :func:`generation_cache_key` except ``generation_version``:
    a deliberate regeneration (bumped knob) that reproduces identical output
    must keep its original ``generated.at`` so the revision does not change on
    timestamp noise alone. Output that actually changed re-mints the
    timestamp — the fingerprint check in ``BuildCache.stable_generated_at``
    keeps provenance honest.
    """
    version = prompt_version if prompt_version is not None else PROMPT_VERSIONS[prompt_id]
    return sha256_hex(
        "\x00".join((content_hash, prompt_id, version, provider_id, generator_version))
    )


def resolve_provider(name: str) -> ModelProvider:
    """Resolve a configured provider name (available: stub)."""
    if name == "stub":
        return StubModelProvider()
    raise ValueError(
        f"unknown model provider {name!r} — available: 'stub' "
        "(configure [build].model in oknoll.toml)"
    )

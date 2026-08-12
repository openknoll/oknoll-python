"""Run a benchmark under both retrieval conditions and score descriptively.

This is a *descriptive* benchmark, not a controlled study — no
blinding, no statistical tests, no superiority claims. What it keeps from the
original methodology: frozen questions, gold evidence authored before any run,
identical answer contract and trace shape across conditions, equal per-passage
evidence budgets, and full provenance (revision ids, model, embedder, prompt
versions, commit-stable versions) in the result record.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from okf_core import (
    EmbeddingProvider,
    ModelProvider,
    answer_question,
    read_current_revision_id,
)
from okf_core import (
    __version__ as okf_core_version,
)
from okf_core.provider import GENERATOR_VERSION, PROMPT_VERSIONS

from oknoll_eval.benchmark import Benchmark

CONDITIONS = ("pd", "rag")
# 2: rows gained retrieval_hit (gold ∩ evidence_paths) and model_usage
#    (provider-reported input/output tokens for the answer call, or None).
RESULTS_SCHEMA_VERSION = 2


def run_benchmark(
    benchmark: Benchmark,
    *,
    provider: ModelProvider,
    embedder: EmbeddingProvider,
    conditions: Sequence[str] = CONDITIONS,
    today: str | None = None,
    clock: Callable[[], str] | None = None,
    timer: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Every question under every condition, in spec order — one row per run."""
    rows: list[dict[str, Any]] = []
    for question in benchmark.questions:
        bundle_dir: Path = benchmark.bundles[question.bundle]
        for condition in conditions:
            result = answer_question(
                bundle_dir=bundle_dir,
                question=question.question,
                provider=provider,
                condition=condition,
                embedder=embedder if condition == "rag" else None,
                today=today,
                clock=clock,
                timer=timer,
            )
            cited_paths = [c.path for c in result.citations]
            cited_resources = sorted({r for c in result.citations for r in c.resources})
            gold = set(question.gold_evidence)
            evidence_paths = [str(p) for p in result.trace["evidence_paths"]]
            rows.append(
                {
                    "question_id": question.id,
                    "bundle": question.bundle,
                    "class": question.klass,
                    "condition": condition,
                    "abstained": result.abstained,
                    "abstention_appropriate": result.abstained
                    == (question.klass == "unanswerable"),
                    "gold_hit": bool(gold & (set(cited_paths) | set(cited_resources))),
                    # The gold file *itself* reached the evidence set. Citations
                    # mirror the evidence set in both conditions, so this is a
                    # directness measure, not independent recall: RAG retrieves
                    # reference files (the form gold evidence is written in),
                    # while PD retrieves concepts and reaches gold through their
                    # provenance — which gold_hit above credits via resources.
                    "retrieval_hit": bool(gold & set(evidence_paths)),
                    "cited_paths": cited_paths,
                    "cited_resources": cited_resources,
                    "evidence_paths": evidence_paths,
                    "warnings": len(result.warnings),
                    "tool_calls": len(result.trace["tools"]),
                    "spent_tokens": result.trace["budget"]["spent_tokens"],
                    "spent_chars": result.trace["budget"]["spent_chars"],
                    "budget_exhausted": result.trace["budget"]["exhausted"],
                    # Provider-reported tokens for the answer call (None for
                    # the stub and for abstentions) — the cost columns' input.
                    "model_usage": result.trace.get("model_usage"),
                    "latency_ms": result.trace["latency_ms"],
                    "answer": result.answer,
                }
            )

    return {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "benchmark": benchmark.name,
        "benchmark_file": benchmark.path.name,
        "conditions": list(conditions),
        "model": provider.id,
        "embedder": embedder.id,
        "revisions": {
            bundle_id: read_current_revision_id(bundle_dir)
            for bundle_id, bundle_dir in sorted(benchmark.bundles.items())
        },
        "okf_core_version": okf_core_version,
        "generator_version": GENERATOR_VERSION,
        "prompt_versions": dict(PROMPT_VERSIONS),
        "questions": len(benchmark.questions),
        "rows": rows,
    }

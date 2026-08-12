"""Render benchmark results as the descriptive comparison table.

Markdown only, deterministic ordering, medians over means where a distribution
is skewed — and an explicit scope note so the table is never mistaken for a
controlled study.
"""

from __future__ import annotations

from typing import Any

_SCOPE_NOTE = (
    "> Descriptive comparison only: frozen questions and pre-written gold "
    "evidence, equal per-passage evidence budgets, identical answer contract — but no "
    "blinding, no statistical tests, and no general superiority claims."
)

# USD per million tokens (input, output) at public list rates, keyed by the
# provider id recorded in the results. Cost columns render "-" for providers
# not listed here (the stub, unknown models) rather than fabricating a number.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "anthropic:claude-fable-5": (10.00, 50.00),
    "anthropic:claude-opus-5": (5.00, 25.00),
    "anthropic:claude-opus-4-8": (5.00, 25.00),
    "anthropic:claude-opus-4-7": (5.00, 25.00),
    "anthropic:claude-opus-4-6": (5.00, 25.00),
    "anthropic:claude-sonnet-5": (3.00, 15.00),
    "anthropic:claude-sonnet-4-6": (3.00, 15.00),
    "anthropic:claude-haiku-4-5": (1.00, 5.00),
}


def _row_cost_usd(row: dict[str, Any], pricing: tuple[float, float] | None) -> float | None:
    """List-price cost of one row's answer call; None when unpriceable.

    Abstentions make no model call and genuinely cost $0.00; a priced model
    with missing usage on an answered row is unpriceable, not free.
    """
    if pricing is None:
        return None
    if row["abstained"]:
        return 0.0
    usage = row.get("model_usage")
    if not isinstance(usage, dict):
        return None
    input_rate, output_rate = pricing
    return (
        int(usage.get("input_tokens", 0)) * input_rate
        + int(usage.get("output_tokens", 0)) * output_rate
    ) / 1_000_000


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _rate(hits: int, total: int) -> str:
    return f"{hits}/{total}" if total else "-"


def render_report(results: dict[str, Any]) -> str:
    rows: list[dict[str, Any]] = results["rows"]
    conditions: list[str] = list(results["conditions"])

    lines: list[str] = [
        f"# PD vs RAG — descriptive benchmark: {results['benchmark']}",
        "",
        _SCOPE_NOTE,
        "",
        f"- spec: `{results['benchmark_file']}` · {results['questions']} question(s)",
        f"- model: `{results['model']}` · embedder: `{results['embedder']}`",
        "- revisions: "
        + ", ".join(f"{bid}=`{rev}`" for bid, rev in sorted(results["revisions"].items())),
        f"- okf-core {results['okf_core_version']} · generator v{results['generator_version']}"
        f" · prompts {results['prompt_versions']}",
        "",
        "## Summary",
        "",
        "| metric | " + " | ".join(conditions) + " |",
        "|---|" + "---|" * len(conditions),
    ]

    def per_condition(metric: str, value: Any) -> str:
        return f"| {metric} | " + " | ".join(str(value(c)) for c in conditions) + " |"

    def rows_for(condition: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["condition"] == condition]

    lines.append(
        per_condition("answered", lambda c: sum(1 for r in rows_for(c) if not r["abstained"]))
    )
    lines.append(
        per_condition("abstained", lambda c: sum(1 for r in rows_for(c) if r["abstained"]))
    )
    lines.append(
        per_condition(
            "gold-evidence hit",
            lambda c: _rate(sum(1 for r in rows_for(c) if r["gold_hit"]), len(rows_for(c))),
        )
    )
    # Directness, not recall: the gold *file itself* reached the evidence set.
    # PD navigates concepts and reaches gold via their recorded provenance
    # (scored by "gold-evidence hit" above), so a low direct rate for PD next
    # to a high hit rate is the architecture, not a retrieval failure.
    lines.append(
        per_condition(
            "gold file retrieved directly",
            lambda c: _rate(
                sum(1 for r in rows_for(c) if r.get("retrieval_hit")), len(rows_for(c))
            ),
        )
    )
    lines.append(
        per_condition(
            "abstention appropriate",
            lambda c: _rate(
                sum(1 for r in rows_for(c) if r["abstention_appropriate"]), len(rows_for(c))
            ),
        )
    )
    lines.append(
        per_condition("trust warnings surfaced", lambda c: sum(r["warnings"] for r in rows_for(c)))
    )
    lines.append(
        per_condition("median tokens", lambda c: _median([r["spent_tokens"] for r in rows_for(c)]))
    )
    lines.append(
        per_condition(
            "median latency (ms)", lambda c: _median([r["latency_ms"] for r in rows_for(c)])
        )
    )
    lines.append(
        per_condition(
            "mean tool calls",
            lambda c: (
                f"{sum(r['tool_calls'] for r in rows_for(c)) / len(rows_for(c)):.1f}"
                if rows_for(c)
                else "-"
            ),
        )
    )
    lines.append(
        per_condition(
            "budget exhausted", lambda c: sum(1 for r in rows_for(c) if r["budget_exhausted"])
        )
    )

    pricing = _PRICING_USD_PER_MTOK.get(str(results["model"]))

    def condition_cost(condition: str) -> str:
        costs = [_row_cost_usd(r, pricing) for r in rows_for(condition)]
        if not costs or any(c is None for c in costs):
            return "-"
        return f"${sum(c for c in costs if c is not None):.4f}"

    lines.append(per_condition("est. cost (USD, list price)", condition_cost))

    lines += [
        "",
        "## Per-question",
        "",
        "| question | class | condition | abstained | gold hit | retrieved | tokens | tools "
        "| cited |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cited = ", ".join(f"`{p}`" for p in row["cited_paths"]) or "-"
        lines.append(
            f"| {row['question_id']} | {row['class']} | {row['condition']} "
            f"| {'yes' if row['abstained'] else 'no'} "
            f"| {'yes' if row['gold_hit'] else 'no'} "
            f"| {'yes' if row.get('retrieval_hit') else 'no'} "
            f"| {row['spent_tokens']} | {row['tool_calls']} | {cited} |"
        )
    lines.append("")
    return "\n".join(lines)

"""Render benchmark results as the descriptive comparison table (ADR-0005).

Markdown only, deterministic ordering, medians over means where a distribution
is skewed — and an explicit scope note so the table is never mistaken for a
controlled study.
"""

from __future__ import annotations

from typing import Any

_SCOPE_NOTE = (
    "> Descriptive comparison only (ADR-0005): frozen questions and pre-written gold "
    "evidence, equal per-passage evidence budgets, identical answer contract — but no "
    "blinding, no statistical tests, and no general superiority claims."
)


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

    lines += [
        "",
        "## Per-question",
        "",
        "| question | class | condition | abstained | gold hit | tokens | tools | cited |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cited = ", ".join(f"`{p}`" for p in row["cited_paths"]) or "-"
        lines.append(
            f"| {row['question_id']} | {row['class']} | {row['condition']} "
            f"| {'yes' if row['abstained'] else 'no'} "
            f"| {'yes' if row['gold_hit'] else 'no'} "
            f"| {row['spent_tokens']} | {row['tool_calls']} | {cited} |"
        )
    lines.append("")
    return "\n".join(lines)

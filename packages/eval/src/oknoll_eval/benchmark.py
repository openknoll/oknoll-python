"""Benchmark spec: frozen bundles + questions with pre-written gold evidence.

The spec is a TOML file (ADR-0005: 20-30 questions over two bundles, gold
evidence authored before any run):

```toml
[benchmark]
name = "a2k-v1"

[bundles]
a2k = "a2k-demo/bundle"          # paths resolve relative to this file

[[questions]]
id = "q01"
bundle = "a2k"
class = "lookup"                 # lookup|synthesis|multi-hop|trust|unanswerable
question = "What is A2K?"
gold_evidence = ["references/source-004.md"]
```

`gold_evidence` entries are bundle-relative paths; a run scores a hit when any
citation path *or* citation resource matches one. Unanswerable questions carry
empty gold evidence — the correct behavior there is abstention.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

QUESTION_CLASSES = ("lookup", "synthesis", "multi-hop", "trust", "unanswerable")


class BenchmarkError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    bundle: str
    question: str
    klass: str
    gold_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Benchmark:
    name: str
    path: Path  # the spec file, for provenance in reports
    bundles: dict[str, Path]  # bundle id → bundle directory
    questions: tuple[Question, ...]


def load_benchmark(path: Path) -> Benchmark:
    """Parse and validate a benchmark spec; all paths resolve against the file."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise BenchmarkError(f"{path} is not valid TOML: {exc}") from exc

    name = str(data.get("benchmark", {}).get("name", "")).strip()
    if not name:
        raise BenchmarkError(f"{path}: [benchmark].name is required")

    root = path.resolve().parent
    bundles: dict[str, Path] = {}
    for bundle_id, rel in data.get("bundles", {}).items():
        bundle_dir = (root / str(rel)).resolve()
        if not bundle_dir.is_dir():
            raise BenchmarkError(f"{path}: bundle {bundle_id!r} not found at {bundle_dir}")
        bundles[str(bundle_id)] = bundle_dir
    if not bundles:
        raise BenchmarkError(f"{path}: [bundles] must name at least one bundle")

    questions: list[Question] = []
    seen_ids: set[str] = set()
    for entry in data.get("questions", []):
        question_id = str(entry.get("id", "")).strip()
        bundle_id = str(entry.get("bundle", "")).strip()
        text = str(entry.get("question", "")).strip()
        klass = str(entry.get("class", "lookup")).strip()
        gold = entry.get("gold_evidence", [])
        if not question_id or question_id in seen_ids:
            raise BenchmarkError(f"{path}: every question needs a unique id ({question_id!r})")
        seen_ids.add(question_id)
        if bundle_id not in bundles:
            raise BenchmarkError(
                f"{path}: question {question_id} names unknown bundle {bundle_id!r}"
            )
        if not text:
            raise BenchmarkError(f"{path}: question {question_id} has no question text")
        if klass not in QUESTION_CLASSES:
            raise BenchmarkError(
                f"{path}: question {question_id} class {klass!r} not in {QUESTION_CLASSES}"
            )
        if not isinstance(gold, list) or not all(isinstance(g, str) for g in gold):
            raise BenchmarkError(
                f"{path}: question {question_id} gold_evidence must be a string list"
            )
        if klass == "unanswerable" and gold:
            raise BenchmarkError(
                f"{path}: question {question_id} is unanswerable but carries gold evidence"
            )
        if klass != "unanswerable" and not gold:
            raise BenchmarkError(
                f"{path}: question {question_id} needs gold_evidence authored before any run"
            )
        questions.append(
            Question(
                id=question_id,
                bundle=bundle_id,
                question=text,
                klass=klass,
                gold_evidence=tuple(gold),
            )
        )
    if not questions:
        raise BenchmarkError(f"{path}: [[questions]] must contain at least one question")

    return Benchmark(name=name, path=path.resolve(), bundles=bundles, questions=tuple(questions))

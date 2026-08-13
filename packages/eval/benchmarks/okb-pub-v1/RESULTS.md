# okb-pub-v1 — published results (2026-08-13)

One manual run of the frozen spec against a real answer model. Full artifacts —
per-question rows, answers, provenance — are committed under
`results-claude-sonnet-5/`; the summary below is reproduced from the generated
report.

> Descriptive comparison only: frozen questions and pre-written gold evidence,
> equal per-passage evidence budgets, identical answer contract — but no
> blinding, no statistical tests, and no general superiority claims.

## Provenance

| | |
|---|---|
| Answer model (both conditions) | `anthropic:claude-sonnet-5` |
| Embedder (rag condition) | `ollama:nomic-embed-text` |
| Bundles | stub-built from the pins: handbook `rev-6d9343b3e29b`, ripgrep `rev-6a0aa0b63a08` |
| okf-core | 0.3.1 · generator v3 · all prompt versions 1 |
| Results schema | v2 (`eval-okb-pub-v1-results.json`) |

The bundles are the deterministic stub builds anyone reproduces byte-for-byte
from the README's pins — the real model only writes the final answers. That is
a deliberate trade: reproducibility of the corpus over generation quality (see
Limitations).

**Ask-policy note (2026-08-13):** this run predates the ask evidence-policy
changes landed on develop later the same day (`ff4c674` scaffolding exclusion,
`898fdae` diversity-first evidence slots, `e92bb89` title-shaped structural
fill). PD rows from runs on okf-core > 0.3.1 are not comparable to this table —
retrieval selects different excerpts, which can shift every PD metric,
gold-evidence hit included. The rag condition is unchanged, so comparing a
newer PD column against this table's rag column would misattribute a policy
change to the answer model. Traces are distinguishable: post-change runs carry
`max_evidence`/`excerpt_chars`/`title_shaped_fill` in the trace `policy` block;
this run's traces do not.

## Summary

| metric | pd | rag |
|---|---|---|
| answered | 26 | 26 |
| abstained | 0 | 0 |
| gold-evidence hit | 15/26 | **19/26** |
| gold file retrieved directly | 0/26 | 19/26 |
| abstention appropriate | 22/26 | 22/26 |
| trust warnings surfaced | **206** | 0 |
| median tokens (retrieval budget) | 5430 | 406 |
| median latency (ms) | 3104 | 3476 |
| mean tool calls | 12.8 | 1.0 |
| est. cost (USD, list price) | $0.1074 | $0.1213 |

Gold-evidence hit by question class:

| class | pd | rag |
|---|---|---|
| lookup | 6/8 | **8/8** |
| synthesis | 3/4 | 3/4 |
| multi-hop | 3/6 | **5/6** |
| trust | 3/4 | 3/4 |
| unanswerable (hits n/a — scored on abstention) | 0/4 appropriate | 0/4 appropriate |

## Reading the numbers — losses first

- **RAG wins evidence hit rate, 19/26 vs 15/26.** It sweeps lookups (8/8) and
  leads on multi-hop (5/6 vs 3/6) — the class PD's link-following was designed
  for. Vector similarity over reference chunks found the gold file more often
  than lexical navigation over stub-generated concepts.
- **Both conditions failed every unanswerable question.** Neither abstained on
  the four questions whose answers are absent from the bundles; both answered
  all 26. PD's abstention is decided by deterministic evidence presence, and
  lexical search always surfaced *something*; RAG's top-k always returns
  passages. The abstention-appropriate 22/26 on both sides is exactly those
  four failures.
- **What PD delivers that RAG structurally cannot: the trust channel.** 206
  warnings (draft/deprecated/stale/unverified evidence, next to the affected
  claims) vs 0 — reference snapshots carry no status metadata, so the RAG
  condition has nothing to warn from. On this corpus that is the clearest
  architectural difference the benchmark measures.
- **Directness is architecture, not failure:** PD's 0/26 "gold file retrieved
  directly" next to 15/26 gold hits means PD reaches gold through concept
  provenance (its citations carry the source references), while RAG retrieves
  the raw reference files the gold paths are written in.
- **Cost is a wash at this scale**: ~$0.23 list total for all 52 answer calls.
  PD spends ~13× more retrieval-context tokens than RAG for its navigation
  (median 5430 vs 406) at comparable answer-call cost and latency.

## Limitations

- The bundles are **stub-built**: concept planning and descriptions are the
  deterministic CI stub's output, so PD navigates its floor-quality concept
  layer. A real-model-built bundle might navigate better — but would not be
  byte-reproducible by others, which the benchmark values more.
- One run, one answer model, no repetition — a descriptive snapshot, not a
  controlled study. The frozen spec and committed artifacts exist so anyone can
  re-run and compare.
- Gold-evidence hit is scored against citations (and their recorded
  provenance), not answer text quality; a correct-sounding answer citing the
  wrong file scores as a miss, and vice versa.

## Reproduce

Build the bundles per `README.md` (pins + `GITHUB_TOKEN`), then:

```sh
oknoll eval spec.toml --model anthropic:claude-sonnet-5 --embedder ollama:nomic-embed-text
```

The spec is frozen: any change to questions or gold evidence is `okb-pub-v2`.

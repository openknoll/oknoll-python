# okb-pub-v1 — published results

Manual runs of the frozen spec against a real answer model. Each published run
is one immutable folder under `runs/`, named `YYYY-MM-DD-<model>`, holding the
generated report and the full results JSON (per-question rows, answers,
provenance). The summary below is reproduced from the latest run's report;
earlier runs stay untouched in their folders.

> Descriptive comparison only: frozen questions and pre-written gold evidence,
> equal per-passage evidence budgets, identical answer contract — but no
> blinding, no statistical tests, and no general superiority claims.

## Runs

| run | okf-core | revisions (handbook · ripgrep) | gold hit pd | gold hit rag |
|---|---|---|---|---|
| [`2026-08-15-claude-sonnet-5`](runs/2026-08-15-claude-sonnet-5/eval-okb-pub-v1-report.md) (latest) | 0.3.3 | `rev-426d78496f5a` · `rev-d47e22347f12` | 16/26 | 19/26 |
| [`2026-08-13-claude-sonnet-5`](runs/2026-08-13-claude-sonnet-5/eval-okb-pub-v1-report.md) | 0.3.1 | `rev-6d9343b3e29b` · `rev-6a0aa0b63a08` | 15/26 | 19/26 |

**Ask-policy note (2026-08-13):** the `2026-08-13` run predates the ask
evidence-policy changes landed on develop later the same day (`ff4c674`
scaffolding exclusion, `898fdae` diversity-first evidence slots, `e92bb89`
title-shaped structural fill). PD rows from runs on okf-core > 0.3.1 are not
comparable to that run's — retrieval selects different excerpts, which can
shift every PD metric, gold-evidence hit included. The rag condition is
unchanged, so comparing a newer PD column against the older rag column would
misattribute a policy change to the answer model. Traces are distinguishable:
post-change runs carry `max_evidence`/`excerpt_chars`/`title_shaped_fill` in
the trace `policy` block; the `2026-08-13` run's traces do not.

**Concept-type note (2026-08-15):** concept frontmatter `type` was renamed
`Reference` → `Concept` on develop after the `2026-08-13` run. Stub rebuilds
from the README's pins on okf-core > 0.3.3 therefore produce different concept
bytes and different revision IDs than that run: handbook `rev-6d9343b3e29b` →
`rev-426d78496f5a`, ripgrep `rev-6a0aa0b63a08` → `rev-d47e22347f12`
(per-concept diff: the `type` line plus the `generated.by` version stamp).
Questions, gold evidence, and `references/` paths are unaffected — the change
is cosmetic frontmatter only and does not alter retrieval or answers.

## Latest run — 2026-08-15, claude-sonnet-5

### Provenance

| | |
|---|---|
| Answer model (both conditions) | `anthropic:claude-sonnet-5` |
| Embedder (rag condition) | `ollama:nomic-embed-text` |
| Bundles | stub-built from the pins: handbook `rev-426d78496f5a`, ripgrep `rev-d47e22347f12` |
| okf-core | 0.3.3 · generator v3 · all prompt versions 1 |
| Results schema | v2 (`eval-okb-pub-v1-results.json`) |

The bundles are the deterministic stub builds anyone reproduces byte-for-byte
from the README's pins — the real model only writes the final answers. That is
a deliberate trade: reproducibility of the corpus over generation quality (see
Limitations).

### Summary

| metric | pd | rag |
|---|---|---|
| answered | 26 | 26 |
| abstained | 0 | 0 |
| gold-evidence hit | 16/26 | **19/26** |
| gold file retrieved directly | 0/26 | 19/26 |
| abstention appropriate | 22/26 | 22/26 |
| trust warnings surfaced | **206** | 0 |
| median tokens (retrieval budget) | 5426 | 406 |
| median latency (ms) | 2360 | 2914 |
| mean tool calls | 12.8 | 1.0 |
| est. cost (USD, list price) | $0.1018 | $0.1257 |

Gold-evidence hit by question class:

| class | pd | rag |
|---|---|---|
| lookup | 7/8 | **8/8** |
| synthesis | 3/4 | 3/4 |
| multi-hop | 3/6 | **5/6** |
| trust | 3/4 | 3/4 |
| unanswerable (hits n/a — scored on abstention) | 0/4 appropriate | 0/4 appropriate |

## Reading the numbers — losses first

- **RAG wins evidence hit rate, 19/26 vs 16/26.** It sweeps lookups (8/8) and
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
  directly" next to 16/26 gold hits means PD reaches gold through concept
  provenance (its citations carry the source references), while RAG retrieves
  the raw reference files the gold paths are written in.
- **Cost is a wash at this scale**: ~$0.23 list total for all 52 answer calls.
  PD spends ~13× more retrieval-context tokens than RAG for its navigation
  (median 5426 vs 406) at comparable answer-call cost and latency.

Against the `2026-08-13` run, every metric is stable except PD gold-evidence
hit (15→16, the extra hit in lookup: 6/8→7/8) — consistent with the ask-policy
changes noted above; the rag condition's profile is unchanged.

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
oknoll eval spec.toml --model anthropic:claude-sonnet-5 --embedder ollama:nomic-embed-text --out runs/<date>-<model>
```

The spec is frozen: any change to questions or gold evidence is `okb-pub-v2`.

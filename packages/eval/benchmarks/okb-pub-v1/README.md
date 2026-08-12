# okb-pub-v1 — public PD-vs-RAG benchmark

A descriptive comparison of progressive-disclosure (PD) answering vs a
vector-RAG baseline
over two public corpora. **Frozen on publication**: the questions and gold
evidence in `spec.toml` never change; a revision means a new spec under a new
name (the same rule that governs the private a2k-v1 regression set).

This benchmark ships **pins, not a corpus**. No third-party content is
redistributed here: the spec records public GitHub repositories pinned to
exact commit SHAs, and `oknoll build` reconstructs each bundle
deterministically from those pins. Reproducing our numbers is the same
operation as auditing them.

## Corpora

| Bundle | Source | Pinned commit | Selected files | License |
|---|---|---|---|---|
| `handbook` | [gsa-tts/handbook](https://github.com/gsa-tts/handbook) (legacy name `18F/handbook`) | `c220f93896b39e9e4ab0f486a1d5bf346ac06e3e` | 250 | CC0-1.0 (US Government work) |
| `ripgrep` | [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep) | `3fce3b5bb0236da2df6d99672afb8a719642eca7` | 25 | MIT OR Unlicense |

The org-knowledge bundle is the TTS handbook: canonical public organizational
knowledge, dense cross-references, public domain. (The GitLab handbook, the
original candidate, lives on gitlab.com — only the GitHub connector pins
commit SHAs, so it is not reproducible under the current connector set.) The
code bundle is ripgrep: well known, permissively licensed, with rich
cross-document structure (GUIDE, FAQ, release checklist, per-crate READMEs).

"Selected files" is what the GitHub connector's default include set
(`README*`, `docs/*`, `*.md`, `*.markdown`, `*.txt`) picks out of the pinned
tree. Sources are numbered `source-001…N` in lexicographic path order at the
pinned SHA — the mapping is deterministic, and the full path↔source-id tables
are committed here as `sources-handbook.tsv` and `sources-ripgrep.tsv` so
every `gold_evidence` entry in the spec can be audited against the file that
carries the answer.

## Reproducing the bundles

From this directory:

```sh
oknoll init handbook
(cd handbook && oknoll add https://github.com/gsa-tts/handbook/tree/c220f93896b39e9e4ab0f486a1d5bf346ac06e3e && oknoll build)

oknoll init ripgrep
(cd ripgrep && oknoll add https://github.com/BurntSushi/ripgrep/tree/3fce3b5bb0236da2df6d99672afb8a719642eca7 && oknoll build)
```

Because the sources are pinned to immutable commits and builds are
deterministic, the resulting bundles are byte-identical for everyone. The
built `handbook/` and `ripgrep/` project directories are local artifacts and
are not committed.

Set `GITHUB_TOKEN` (any token with public-repo read access) before building:
the handbook fetch makes ~250 GitHub API blob requests, which exceeds the
unauthenticated rate limit of 60/hour.

## Running the benchmark

```sh
oknoll eval spec.toml --model anthropic --embedder ollama:nomic-embed-text
```

CI runs stay on the deterministic stub (`--model stub --embedder stub`);
published numbers come from manual runs against real providers, with full
provenance (revision ids, model, embedder, prompt versions) captured in the
results JSON.

## Question design

26 questions, authored 2026-08-12 against the pinned snapshots, gold evidence
written **before any run**. Distribution: 8 `lookup`, 4 `synthesis`,
6 `multi-hop`, 4 `trust`, 4 `unanswerable`, split 13/13 across the two
bundles.

Contamination defense, per the plan:

- **Snapshot-grounded facts.** Lookups target facts specific to the pinned
  revisions (a 2025 decommission date, a target triple added in a 2026
  release, org codes, named tools) rather than trivia a model memorizes.
- **Punished contamination.** The `unanswerable` questions are ones the public
  internet *can* answer but the pinned bundles cannot (why 18F was shut down,
  which editor embeds ripgrep). A system answering from parametric memory is
  by definition ungrounded there; abstention is scored as correct.
- **Trust questions reward provenance.** Their answers exist in the bundles
  but come from dated or proposal-stage documents (2021 COVID travel
  guidance, an approved RFC, an unwritten changelog section, a 2022 benchmark
  run). Groundedness columns — gold hit, cited, abstention appropriateness —
  carry the scoring, not trivia recall.

## Freezing rule

Once published, `spec.toml` is immutable. Fixing a question, changing gold
evidence, or re-pinning a corpus requires a new spec name (`okb-pub-v2`), so
published numbers stay comparable forever.

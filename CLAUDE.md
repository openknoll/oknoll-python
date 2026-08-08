# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`oknoll-python`: the OpenKnoll Python core and CLI — a local-first knowledge packaging
system. It ingests documents/websites/repos, normalizes them into a format-neutral
**CanonicalDoc** model, and serializes portable **OKF v0.2** Markdown bundles explored via
a deterministic navigation layer (CLI + local MCP). This repo is fully useful offline —
no cloud account or service.

This is one of four sibling repos (ADR-0006 split of the original `openknoll` monorepo,
all under the `openknoll` GitHub org). **This repo is PUBLIC** — never add internal
documents, benchmarks over them, credentials, or operational notes here.
- **oknoll-main** (private) — all project documents: design doc, implementation plan,
  ADRs, sprint handovers, notes, frozen benchmark.
- **oknoll-python** (this repo, public) — `packages/{okf-core, oknoll-cli, connectors,
  providers, eval}`, fixtures, scripts. The source of the shared core.
- **oknoll-services** (private) — GCP side (Cloud Run services, pipeline worker,
  Terraform). Consumes `okf-core`/`oknoll-connectors` from this repo via pinned git-tag
  dependencies — tag releases here (`v0.x.y`) when the services need new core behavior.
- **oknoll-app** (private) — Next.js web app on Vercel; talks to oknoll-services over
  HTTP only.

## Authoritative documents (in the private `openknoll/oknoll-main` repo)

- `openknoll-merged-design-solo.md` — the design source of truth.
- `implementation-plan.md` — execution plan: phase order with exit gates. Where it
  deviates from the design (branching only), the plan wins.
- `adr/` — decision records. Changing a binding decision (design §2 table, CLI
  surface, etc.) requires an ADR there, not a silent code change.

## Non-negotiable constraints

- **The file is the contract.** Canonical bundles are Markdown trees + deterministic archives.
- **Published revisions are immutable.** A rebuild writes a new revision directory and
  atomically advances the current pointer.
- **The CLI surface is frozen** (design §5.1): `init`, `add`, `build`, `lint`, `ask`,
  `chat`, `serve --mcp`, `pack`, `diff --check`, `plugin`, `login`, `keys`, `eval`.
  No aliases from superseded designs (`sync`, `validate`, `explore`, `export`).
- **The deterministic explorer and the PD-vs-RAG evaluation are never cut.**
- **Determinism everywhere:** paths, manifests, checksums, indexes, archives, link graphs.
  Model-generated fields are cached by content hash + prompt version + model ID; model calls
  sit behind a provider interface with a deterministic stub for CI.
- **OKF posture:** strict producer, permissive consumer — tolerate unknown types/keys in
  others' bundles and preserve unknown fields on round trips.
- **Security invariants:** bundle text is untrusted data — it never triggers tools or
  changes policy. okf-core imports no network module (structurally tested); real providers
  live in `packages/providers`. All connector network I/O goes through `SafeFetcher`
  (SSRF enforcement). Connectors acquire and normalize only — they never write OKF;
  okf-core owns generation, validation, indexing, packing.
- **Benchmark a2k-v1 is frozen** (`benchmark/` in oknoll-main): questions/gold evidence
  must not change — a revision means a new spec name (`a2k-v2`).

## Layout

- `packages/okf-core` — CanonicalDoc, OKF parser/writer, 5-level lint, pipeline stages
  (acquire→normalize→plan→generate→link→lint→index→publish), explorer (seven deterministic
  tools: `overview/list/search/peek/read/links/history`), ask policy, RAG seam, packer.
- `packages/oknoll-cli` — Typer CLI over okf-core; includes the stdio MCP server
  (`oknoll serve --mcp`), chat, plugin surface.
- `packages/connectors` — files/web/github connectors + `SafeFetcher` + the shared
  `ConnectorContractSuite` every connector must pass.
- `packages/providers` — real model/embedding providers (Anthropic, Ollama); spec grammar
  `stub | anthropic[:<model>] | ollama:<model>`.
- `packages/eval` — benchmark spec + runner over PD and RAG conditions.
- Conversations/traces/indexes are derived state under `bundle/.oknoll/` — never packed,
  never bundle content.

## Tooling

- Python 3.12+ (`uv` workspace, `ruff`, `mypy --strict`, `pytest`, Typer).
- `Makefile` targets `install/lint/typecheck/test/security/build/smoke` are the single
  entrypoint — CI reuses them.
- `make security` runs the release-gate corpora (SSRF + prompt-injection + injected-sources);
  a failure stops a release.

## Branches

Two long-lived branches: `develop` (integration) and `main` (promoted only by PR from
`develop`). Feature branches come off `develop`; never commit to `main` directly (ADR-0001).
Standing preference: when a feature branch is complete and checks are green, merge into
`develop` (`git merge --no-ff`), re-run `make test` on `develop`, and push.

## Testing expectations

Golden fixtures are the backbone: frozen inputs → expected trees/reports/checksums in
`fixtures/` (upstream OKF reference bundles + malformed cases auto-discovered). Every
connector must pass `ConnectorContractSuite`. Security suites are release gates.
Evaluation runs in CI against the deterministic model stub — CI never touches the network.

Gotchas:
- Test files across packages need unique basenames (pytest rootdir import mode).
- Exploring a bundle writes derived state into it (`.oknoll/index/...`) — tests must
  `shutil.copytree` a fixture to `tmp_path` before exploring it.
- macOS: if a long-lived `oknoll` process dies on import with `ModuleNotFoundError`,
  run `chflags -R nohidden .venv` once (historical iCloud hidden-flag issue, believed
  resolved outside iCloud-synced paths).

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`oknoll-python`: the OpenKnoll Python core and CLI — a local-first knowledge packaging
system. It ingests documents/websites/repos, normalizes them into a format-neutral
**CanonicalDoc** model, and serializes portable **OKF v0.2** Markdown bundles explored via
a deterministic navigation layer (CLI + local MCP). This repo is fully useful offline —
no cloud account or service.

**This repo is PUBLIC** — never add internal documents, private benchmarks,
credentials, or operational notes here. It is standalone: everything needed to
work in it lives in this file, the README, and the code. The hosted OpenKnoll
platform consumes `okf-core`/`oknoll-connectors` as pinned git-tag
dependencies — tag releases (`v0.x.y`) when downstream consumers need new core
behavior.

## Non-negotiable constraints

- **The file is the contract.** Canonical bundles are Markdown trees + deterministic archives.
- **Published revisions are immutable.** A rebuild writes a new revision directory and
  atomically advances the current pointer.
- **The CLI surface is frozen**: `init`, `add`, `build`, `lint`, `ask`,
  `chat`, `serve --mcp`, `pack`, `diff --check`, `plugin`, `login`, `keys`, `eval`,
  `viz` (added 2026-08-09: renders the link graph to one self-contained offline
  HTML file), `config`, `doctor` (added 2026-08-09: machine-level config surface
  over `~/.oknoll/` — `login`/`keys` stay reserved for the hosted control plane).
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
- **The a2k-v1 benchmark spec is frozen**: questions/gold evidence must not
  change — a revision means a new spec name (`a2k-v2`).

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
- Config layering (`oknoll_cli/global_config.py`): settings resolve CLI flag >
  bundle `oknoll.toml` > `~/.oknoll/config.toml` > `"stub"`; secrets are
  environment-only (shell > project `.env` > `~/.oknoll/.env`). Secrets never
  go in any TOML; `oknoll eval` never inherits settings from config files.

## Branches

Two long-lived branches: `develop` (integration) and `main` (promoted only by PR from
`develop`). Feature branches come off `develop`; never commit to `main` directly.
Standing preference: when a feature branch is complete and checks are green, merge into
`develop` (`git merge --no-ff`), re-run `make test` on `develop`, and push.

## Testing expectations

Golden fixtures are the backbone: frozen inputs → expected trees/reports/checksums in
`fixtures/` (upstream OKF reference bundles + malformed cases auto-discovered). Every
connector must pass `ConnectorContractSuite`. Security suites are release gates.
Evaluation runs in CI against the deterministic model stub — CI never touches the network.

Gotchas:
- Test files across packages need unique basenames (pytest rootdir import mode).
- The repo-root `conftest.py` pins `OKNOLL_HOME` to a tmp dir and restores
  `os.environ` after every test — without it, a developer's real `~/.oknoll`
  (or an env var leaked by `load_env`'s setdefault) would bleed into the suite.
  It must stay at the root: a second `tests/conftest.py` trips mypy's
  duplicate-module rule against okf-core's.
- Exploring a bundle writes derived state into it (`.oknoll/index/...`) — tests must
  `shutil.copytree` a fixture to `tmp_path` before exploring it.
- macOS: if a long-lived `oknoll` process dies on import with `ModuleNotFoundError`,
  run `chflags -R nohidden .venv` once (historical iCloud hidden-flag issue, believed
  resolved outside iCloud-synced paths).

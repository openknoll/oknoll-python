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
- **The CLI surface is namespaced and frozen** (v0.4.0 clean break, 2026-08-14):
  `oknoll <resource> <command>` across twelve namespaces — `project source
  bundle image registry auth daemon query mcp ui config system` — pinned by
  per-namespace set-equality tests in `packages/oknoll-cli/tests/test_cli.py`
  (the frozen surface, including which commands are still "not yet" stubs,
  lives there). The flat v0.3 commands are hidden pointer stubs: they print
  `moved: use 'oknoll X Y'` and exit 2, never perform work (`config *` is the
  one surface kept in place; `keys` is dropped — its stub points to `auth
  login`). The README's "Upgrading from v0.3" table is the mapping of record.
  Adding or renaming a command or namespace is a deliberate act: update the
  frozen sets, the README table, and this file together. No aliases from
  superseded designs (`sync`, `validate`, `explore`, `export`).
- **The deterministic explorer and the PD-vs-RAG evaluation are never cut.**
- **Determinism everywhere:** paths, manifests, checksums, indexes, archives, link graphs.
  Model-generated fields (concept plans and descriptions) are cached by content hash +
  prompt version + model ID + the project's `[build].generation_version` (the deliberate
  regeneration knob); model calls sit behind a provider interface with a deterministic
  stub for CI. The concept planner's decisions are bounded and validated: the model only
  groups outline sections and names the groups — unusable output falls back to one
  concept per document, and the fallback is cached so `diff --check` stays green.
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
- `packages/oknoll-cli` — Typer CLI over okf-core (`oknoll_cli/commands/` holds one
  module per namespace); includes the stdio MCP server (`oknoll mcp stdio`), chat,
  plugin surface.
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
- Releases are tag-driven (`release.yml`): all five packages share one version,
  moved in lockstep by `scripts/bump_version.py <version>` (pyprojects, exact
  sibling pins, `__version__` strings) + `make install` to refresh `uv.lock`;
  the workflow refuses a tag that disagrees with the pyproject versions, then
  gates, builds (`uv build --all-packages`), publishes via PyPI trusted
  publishing (one job per package, environment `pypi-<dist>` — PyPI requires
  pending publishers to have unique configs), and cuts a GitHub Release. The
  CLI's distribution name is `oknoll` (import package stays `oknoll_cli`).
- Config layering (`oknoll_cli/global_config.py`): settings resolve CLI flag >
  bundle `oknoll.toml` > `~/.oknoll/config.toml` > `"stub"`; secrets are
  environment-only (shell > project `.env` > `~/.oknoll/.env`). Secrets never
  go in any TOML; `oknoll eval` never inherits settings from config files.

## Branches

Two long-lived branches: `develop` (integration) and `main` (promoted only by PR from
`develop`). Feature branches come off `develop`; never commit to `main` directly.
**Temporary during the v0.4.0 runtime evolution:** `preview` is the evolution
integration branch — evolution feature branches merge there instead of `develop`
(CI runs on it; `v0.4.0aN` alpha tags are cut from it); it merges into `develop`
at v0.4.0 stabilization and is then deleted.
Standing preference: when a feature branch is complete and checks are green, merge into
`develop` (`git merge --no-ff`), re-run `make test` on `develop`, and push.

## Local agent state & worktrees

- `.agents/` (gitignored) is local planning space — `plans/` for design/task/
  implementation plans, `handovers/` for session-to-session memory. Never commit
  it or reference it from committed code or docs. It exists only in the main
  checkout: agents in a worktree read/write the main checkout's `.agents/` by
  path, never a copy.
- In-repo worktrees live under `.claude/worktrees/` (gitignored). A fresh
  worktree must run `make install` for its own `.venv` — never copy a venv
  (scripts hardcode absolute paths). `.worktreeinclude` carries `.env` and
  `.claude/settings.local.json` into new worktrees.

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

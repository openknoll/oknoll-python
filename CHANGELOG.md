# Changelog

## v0.4.0 — 2026-08-15

### The one breaking change: resource namespaces

The flat v0.3 command surface is replaced by resource namespaces
(`oknoll <resource> <command>`) in one clean break. Every old command prints
its replacement and exits 2 — nothing breaks silently, and no old spelling
performs work:

| v0.3 (flat) | v0.4 (namespaced) |
|---|---|
| `init` | `project init` |
| `add` | `source add` |
| `build` | `project build` |
| `lint` | `bundle lint` |
| `ask` | `query ask` |
| `chat` | `query chat` |
| `serve --mcp` | `mcp stdio [BUNDLE]` |
| `pack` | `bundle pack` |
| `diff` | `bundle diff` |
| `plugin *` | `system plugins *` |
| `login` | `auth login` |
| `keys` | dropped — `auth login` is the whole surface |
| `eval` | `system eval` |
| `viz` | `system viz` |
| `config *` | `config *` (unchanged) |
| `doctor` | `system doctor` |

### New: the portable knowledge runtime

- **Local content store + catalog** (new public package `oknoll-runtime`):
  `bundle install` puts any OKF bundle — directory or archive — into a
  machine-level content-addressed store under a catalog alias, pinned by OCI
  manifest digest and OKF revision id. `bundle list|inspect|checkout|
  uninstall|unpack`, `query ask local:<alias>` (cited, offline).
- **OkNoll images**: deterministic OCI artifacts that *contain* bundles
  (`image build|list|inspect|tag|remove|save|load`); byte-identical digests
  across OSes are CI-enforced.
- **Safe ingestion**: archives extract through a hardened path (no traversal,
  links, or device nodes; compressed/expanded/count/per-file limits; atomic
  publish), backed by a malicious-archive corpus that gates every release.
- **The local daemon**: `daemon start` serves every installed bundle over one
  loopback origin — `/` (minimal web UI), `/api/v1` (HTTP/JSON incl. ask with
  revision-pinned sessions), `/mcp` (MCP Streamable HTTP). Loopback-only,
  bearer-token auth on `/api` and `/mcp`, loopback-origin allowlist,
  path-free errors — all covered by release-gate security tests.
- **Multi-bundle MCP**: one static endpoint and tool surface for agents —
  the seven explorer tools with a required `bundle` argument, plus
  `bundles_list`/`bundles_get`; citations carry immutable identity
  (`alias@sha256:…/path`). `oknoll mcp config --client claude|codex`
  configures an agent once; `oknoll mcp stdio` remains the single-bundle
  fallback and now accepts installed aliases.
- **Daemon chat**: `query chat --bundle a --bundle b` chats across installed
  bundles through the daemon, one revision-pinned session per bundle. Daemon
  ask sessions carry bounded conversation memory: prior question/answer pairs
  reach the prompt as data and the last answer's citations seed the next
  turn's retrieval — the same contract as local `query chat`.
- **Paged read everywhere**: the v0.3.5 `read` cursor (`start_char`/
  `next_start`) is part of the shared toolkit, so stdio MCP, the daemon HTTP
  API, and the daemon MCP all page large documents identically.
- **Locator grammar**: `local:` / `oci://` / `oknoll://` (spec and test
  vectors in-repo); remote locators are parsed today and land with the
  registry client.
- Retrieval conditions are unchanged (`pd`/`rag` in `--mode`); the a2k-v1
  benchmark contract is untouched.

### Build feedback

- `oknoll project build` streams progress to stderr while it runs: each
  source announces itself before acquisition, every planned/generated
  concept and reference is numbered (`generate: concept [3/12] …`), and
  cache hit/miss totals close the generation stage — a stalled build now
  names its current work item instead of sitting silent.

### Packaging

- New PyPI distribution `oknoll-runtime` (daemon + store; depends on
  starlette/uvicorn/httpx/mcp/platformdirs). All six packages version in
  lockstep.
- macOS and Linux are the supported platforms for v0.4; Windows is untested.

## v0.3.5 — 2026-08-16

- **Self-describing bundles**: the index becomes descriptive (per-entry
  descriptions), references and the bundle itself gain descriptions; concept
  descriptions see the whole section outline; word-boundary clipping and
  prose normalization keep them clean.
- **Overview table of contents** and per-hit search descriptions in the
  explorer and MCP tools.
- **Paged read**: `read` gains a `start_char`/`next_start` cursor — large
  documents are fully reachable through bounded calls.
- **Chat remembers the conversation**: history reaches the prompt as clipped
  data and the last answer's citations seed retrieval (PD only).
- **Ask policy v2**: evidence scales with the retrieval budget; the published
  a2k-v1 evaluation stays pinned to policy v1.
- Finer concept planning: richer outline, retuned prompt, bounded re-split.

## v0.3.4 — 2026-08-15

- Generated concepts carry frontmatter type `Concept` (was `Reference`).
- `OllamaEmbedder` splits large inputs into bounded batches.
- okb-pub-v1 results live under `runs/`, one immutable folder per run.

Earlier releases (≤ v0.3.3) predate this changelog; see the GitHub Releases
notes.

# OpenKnoll — Python core + CLI (`oknoll-python`)

OpenKnoll **compiles** messy source material — documents, websites, repositories — into a
trusted knowledge interface for AI agents: a portable, versioned bundle of plain Markdown
that agents navigate deterministically and answer from with citations, trust warnings, and
honest abstention. Local-first and fully useful offline; the file is the contract — a
valid bundle stays useful without an OpenKnoll account, cloud service, or proprietary
reader.

```sh
brew install openknoll/tap/oknoll        # or: uv tool install oknoll
oknoll project init handbook && cd handbook
oknoll source add ./docs                 # plus websites and GitHub repos
oknoll project build                     # compile sources → published revision rev-…
oknoll query ask "How are credentials stored?"  # cited answer — or an honest "not in this bundle"
oknoll bundle install ./bundle --name handbook  # into the local content store, pinned by digest
oknoll daemon start && oknoll ui open    # every installed bundle: web UI + HTTP + MCP, one endpoint
```

## Why not just point the agent at my files (or a vector DB)?

Because the expensive failure isn't "the agent can't see the docs" — it's the agent
confidently answering from stale, unattributed, or missing knowledge, with nobody able
to tell. Raw folders and vector indexes share the same gaps:

- **No provenance.** An answer synthesized from a folder or a top-k of chunks can't say
  *which* source, *which* version, or whether the source was ever reviewed.
- **No abstention.** Similarity search always retrieves *something*, so the model rarely
  says "that isn't in the knowledge base" — it improvises.
- **No quality gate.** Duplicated, contradictory, or abandoned documents are served to
  the agent with the same confidence as the canonical ones.
- **No reproducibility.** Ask again next week — different chunks, different answer, and
  no way to diff what changed in between.

OpenKnoll's answer is a compile step with a contract on the output:

- **Build is a compiler** — acquire → normalize → plan → generate → link → lint → index →
  publish. Defects surface at build time (five-level lint), not answer time, and
  `oknoll bundle lint` reports bundle-health metrics — source coverage, orphaned concepts,
  broken links, staleness, uncited references — as JSON for CI to watch over time.
- **Provenance is pinned** — every concept cites its sources; repositories are pinned to
  the exact commit SHA, and every source carries a content hash.
- **Revisions are immutable** — content-derived revision ids, idempotent rebuilds, and
  `oknoll bundle diff --check` proves a rebuild reproduces the published revision byte for byte.
  `oknoll bundle diff rev-a rev-b` reports what changed between two revisions in knowledge
  terms: concepts added or retitled, sources re-pinned, link edges moved.
- **Navigation is deterministic** — agents explore through seven bounded, read-only tools
  (`overview`, `list`, `search`, `peek`, `read`, `links`, `history`), answer only from
  what they retrieved, cite bundle paths, surface trust warnings (draft, unverified), and
  abstain when the evidence isn't there.
- **The artifact is portable** — plain Markdown plus a checksummed manifest. Version it
  in Git, lint it in CI, `pack` it, hand it to any MCP client.

The mental model: what a container image is to "works on my machine", an OpenKnoll bundle
aims to be to "the agent read our docs".

## Install

The `oknoll` CLI is fully useful offline and local — no cloud account, no service.

With [Homebrew](https://brew.sh) (macOS and Linux; pre-built bottles, no Python
or Rust toolchain needed):

```sh
brew install openknoll/tap/oknoll
oknoll --help
```

Or from PyPI (v0.3.0 onward), into an isolated tool environment:

```sh
uv tool install oknoll     # or: pipx install oknoll
```

macOS and Linux are the supported platforms for v0.4; Windows is not targeted
yet (nothing is deliberately incompatible — it is simply untested).

To hack on it instead, run it out of a clone with [`uv`](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/openknoll/oknoll-python.git
cd oknoll-python && make install     # one-time: sync the uv workspace

# `--project` lets you invoke the CLI from any directory in any later session:
REPO="$PWD"
oknoll() { uv run --no-sync --project "$REPO" oknoll "$@"; }
```

### Upgrading from v0.3

v0.4.0 replaced the flat command surface with resource namespaces
(`oknoll <resource> <command>`) in one clean break. Every old command now prints its
replacement and exits 2 — nothing breaks silently, and no old spelling performs work:

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

## Usage

### Demo: documents + a GitHub repo → portable knowledge bundle

```sh
# 1. Create a project (config, ignore file, bundle skeleton).
mkdir -p ~/demo && cd ~/demo
oknoll project init handbook-demo --name "Team Handbook"
cd handbook-demo

# 2. Register sources. Local files/dirs (md, txt, PDF, docx) are probed on the spot;
#    GitHub repos and websites are registered and fetched at build time.
cp -R "$REPO/fixtures/sources/handbook" sources/handbook   # or use your own docs
oknoll source add sources/handbook
oknoll source add https://github.com/openknoll/oknoll-python   # commit-SHA provenance
# oknoll source add https://your-site.example/docs/        # sitemap-first same-site
                                                           # crawl, robots honored,
                                                           # 100-page cap

# 3. Build: acquire → normalize → plan → generate → link → lint → index → publish.
oknoll project build
# 6 concept(s) from 6 source file(s); cache 0 hit(s), 12 miss(es)
# published revision rev-a4d583b7229c

# 4. The bundle is plain Markdown + a manifest — the file is the contract.
ls bundle/concepts/            # one concept per source document
head bundle/references/source-006.md   # provenance: connector, uri (pinned to the
                                       # exact commit SHA for repos), source hash

# 5. Verify. Rebuilds are idempotent (content-derived revision ids, build cache):
oknoll project build           # cache 12 hit(s) → "no changes"
oknoll bundle lint bundle --strict   # five-level validation; 0 findings
oknoll bundle diff --check     # rebuild to temp, byte-compare — CI reproducibility gate

# 6. Ask a question. The explorer navigates deterministically — overview → search →
#    peek → read → follow links — and answers only from what it retrieved.
oknoll query ask "How are credentials stored?"
# Based on 1 passage(s) retrieved from the bundle:
# - Security policy: Credentials are stored hash-only. Static keys are forbidden.
#   [concepts/security-policy.md]
#
# Citations:
# - concepts/security-policy.md (source-004)
#
# Warnings:
# - concepts/security-policy.md: status is draft — content is unreviewed
# - concepts/security-policy.md: unverified — no verification record
#
# trace: bundle/.oknoll/traces/ask-….json (5 tool call(s), 2876 chars, ~719 tokens)

# With no supporting evidence it abstains and names the gap, rather than inventing:
oknoll query ask "What is the Zanzibar revenue forecast?"
# Insufficient evidence in this bundle to answer. No concept mentions: forecast,
# revenue, zanzibar. Adding a source about … would resolve the gap.

oknoll query ask "…" --json    # answer + citations + warnings + full trace, machine-readable

# 7. Pack a deterministic, checksummed archive for sharing.
oknoll bundle pack             # dist/<name>-rev-….okf.tgz  + .sha256
oknoll bundle pack --profile plain   # same tree with OKF-only frontmatter keys stripped
```

### Install existing bundles into the local store

Any OKF bundle — yours or someone else's, a directory or a packed archive —
installs into a machine-level content-addressed store under a catalog alias.
Archives are ingested through a hardened extraction path (no traversal,
links, or device nodes; size/count limits; atomic publish), the bundle's own
manifest checksums are verified, and the installed tree is immutable and
pinned by both its OCI image digest and its OKF revision id.

```sh
oknoll bundle install ./handbook --name handbook       # or handbook.okf.tgz
oknoll bundle list                                     # alias, revision, digest, title
oknoll query ask local:handbook "How are incidents escalated?"   # cited, offline
oknoll bundle checkout local:handbook ./handbook-copy  # editable working tree
oknoll bundle unpack vendor.okf.tgz ./vendor           # safe extract, nothing registered

# Bundles travel as OCI artifacts (an image *contains* a bundle, never replaces it):
oknoll image build ./handbook --tag handbook:1.0
oknoll image save handbook:1.0 -o handbook.tar         # OCI image layout, air-gap friendly
oknoll image load handbook.tar                         # digest-verified on the way in
```

### Run the local daemon: one endpoint for UI, HTTP API, and MCP

`oknoll daemon start` runs one persistent loopback process that serves every
installed bundle over a single origin (default `http://127.0.0.1:7465`):

- `/` — a minimal web UI: catalog, bundle overview, search, document viewer,
  ask box;
- `/api/v1` — HTTP/JSON: the catalog, the seven explorer tools per bundle,
  and `ask` with revision-pinned sessions;
- `/mcp` — MCP Streamable HTTP: the same seven tools for *every* installed
  bundle (each call names its bundle) plus `bundles_list`/`bundles_get`.

The daemon binds loopback only. `/api` and `/mcp` require a random bearer
token (created `0600` in the state directory on first start); browser origins
are validated against a loopback allowlist; client-visible errors never
contain host paths. Sessions pin content — repointing an alias with
`bundle install --update` never moves a session that is already running.

```sh
oknoll daemon start            # detached; --foreground to stay attached
oknoll daemon status           # health, endpoint, bundle count (--json for scripts)
oknoll ui open                 # browser UI (token travels in the URL fragment)
oknoll daemon logs             # tail the daemon log
oknoll daemon stop
```

#### Configure an agent once, explore every bundle

```sh
oknoll mcp endpoint                          # http://127.0.0.1:7465/mcp
oknoll mcp config --client claude            # print the client config
oknoll mcp config --client claude --install  # register it via the `claude` CLI
oknoll mcp config --client codex             # ~/.codex/config.toml snippet
oknoll mcp inspect                           # tool surface + served bundles
```

Installing or removing bundles never changes the agent config: the endpoint
and tool surface are static, discovery happens through `bundles_list`, and
every result carries the immutable identity (`alias@sha256:…`), so citations
are fully qualified across bundles.

### Serve a bundle to an agent over MCP (stdio fallback)

`oknoll mcp stdio` exposes the bundle to any [MCP](https://modelcontextprotocol.io)
client as a read-only stdio server. It offers exactly the seven deterministic explorer
tools — `overview`, `list`, `search`, `peek`, `read`, `links`, `history` — each with a
JSON schema, bounded output, and path validation. The server adds no capability the local
explorer does not already have: it cannot write, follow a link off disk, reach the
network, or expose anything outside the bundle root. Bundle text stays data. A session
binds to the bundle's current immutable revision at startup, so its answers stay
attributable to one revision id even if a rebuild happens mid-session (bundles without a
revision store — someone else's, or an unpacked archive — are served from the tree as-is).

```sh
oknoll mcp stdio                         # serves the active project's bundle
oknoll mcp stdio /path/to/any/bundle     # or any bundle, incl. someone else's
oknoll mcp stdio handbook                # or an installed alias (local:handbook)
```

It speaks JSON-RPC on stdout and prints status to stderr, so it is driven by an MCP
client, not by hand.

#### Connect it to a local agent (Claude Code)

Register the server once, pointing it at a built bundle (use absolute paths):

```sh
claude mcp add oknoll -- \
  uv run --no-sync --project /abs/path/to/oknoll-python \
  oknoll mcp stdio /abs/path/to/handbook-demo/bundle
```

Then in a `claude` session the seven `oknoll` tools are available, and the agent answers
from the bundle by navigating them — for a multi-hop question it will `search`, `read`,
and follow `links` to a second concept, citing the bundle paths it used. Any MCP client
takes the same shape via a JSON config:

```json
{
  "mcpServers": {
    "oknoll": {
      "command": "uv",
      "args": ["run", "--no-sync", "--project", "/abs/path/to/oknoll-python",
               "oknoll", "mcp", "stdio", "/abs/path/to/bundle"]
    }
  }
}
```

A self-contained, runnable example lives at
[`scripts/agent-demo.py`](scripts/agent-demo.py): an independent Anthropic tool-runner
that spawns the server, answers a multi-hop question through MCP, and prints the tool-call
trace as it goes. It needs `ANTHROPIC_API_KEY` (env or a project `.env`):

```sh
uv run --no-sync python scripts/agent-demo.py            # frozen multi-hop fixture
uv run --no-sync python scripts/agent-demo.py \
  --bundle /abs/path/to/bundle --question "…"            # your own bundle
```

#### Inspect it with MCP Inspector

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) is the standard way to
poke a server by hand. Serving is read-only, so this is safe to run against any bundle.

**Web UI** — launch the Inspector, then configure the connection in the browser:

```sh
npx @modelcontextprotocol/inspector          # opens http://localhost:6274
```

In the left panel set **Transport** to STDIO, **Command** to `uv`, and **Arguments** to
`run --no-sync --project /abs/path/to/oknoll-python oknoll mcp stdio /abs/path/to/bundle`,
then **Connect** → **Tools** → **List Tools** and run e.g. `search` with `query`.

**Headless CLI** — copy [`mcp-inspector.example.json`](mcp-inspector.example.json), edit
the two absolute paths, and drive it non-interactively:

```sh
cp mcp-inspector.example.json /tmp/oknoll-inspector.json   # then edit the paths

npx @modelcontextprotocol/inspector --cli \
  --config /tmp/oknoll-inspector.json --server oknoll --method tools/list

npx @modelcontextprotocol/inspector --cli \
  --config /tmp/oknoll-inspector.json --server oknoll \
  --method tools/call --tool-name search --tool-arg query="how are credentials stored"
```

Two things worth knowing. Inspector's own flags (`--method`, `--tool-name`, `--tool-arg`)
must come **before** the server command; the `--config` form above avoids the issue by
keeping the command in the file. And the CLI merges an install-level config at
`~/.mcp-inspector/mcp.json` — passing `--config` with an explicit `--server oknoll` keeps
the run deterministic regardless of what is stored there.

### Chat interactively

`oknoll query chat` is a single-bundle REPL over the same explorer, pinned to the current
revision. Conversations persist under `bundle/.oknoll/conversations/` (derived state,
never packed) and resume by id:

```sh
oknoll query chat              # ask, ask again; `exit` to quit
oknoll query chat --mode rag   # vector baseline instead of progressive disclosure
oknoll query chat --resume chat-20260807-….   # continue where you left off
```

With installed aliases, chat runs against the daemon instead — one
revision-pinned session per bundle, `@alias` to address another bundle
mid-conversation, answers stamped with the pinned identity:

```sh
oknoll query chat --bundle handbook --bundle acme   # needs `oknoll daemon start`
```

`oknoll system plugins list|inspect|validate` reports the installed connectors and checks them
against the connector protocol.

Revisions are immutable under `bundle/.oknoll/revisions/`; the bundle top level always
materializes the current revision. Every `ask` writes a separate trace record
(tools called, paths read, characters and tokens spent, latency, model, revision id,
retrieval condition) under `bundle/.oknoll/traces/` — derived state, never packed.
Bundle text is data: nothing a bundle says can make the explorer follow a link off
disk, reach the network, or drop a trust warning.

### Configuration

By default `project build`/`query ask`/`query chat` use a deterministic stub model (no network,
reproducible) — that default exists for CI and benchmarks. For bundle quality,
configure a real model: it writes the concept descriptions, and it plans
concept boundaries — splitting a multi-section document into several
topic-scoped concepts instead of one digest per source (the stub always keeps
one concept per document). Real providers are configured in two separate
planes:

- **Settings** (which model/embedder, the Ollama endpoint) resolve
  *specific beats general*: CLI flag (`query ask --model …`) → the bundle's
  `oknoll.toml` → `~/.oknoll/config.toml` → `stub`.
- **Secrets** (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`) never live in TOML. They
  resolve through the environment: shell → `<project>/.env` →
  `~/.oknoll/.env`, so a project `.env` wins for local development.

Set a machine-wide default once instead of repeating it per bundle:

```sh
oknoll config set build.model anthropic:claude-opus-5   # or ollama:llama3
oknoll config set rag.embedder ollama:nomic-embed-text
oknoll config set providers.ollama.host http://127.0.0.1:11434
oknoll config list      # effective settings + where each one comes from
oknoll config get build.model
oknoll system doctor    # home dir, .env permissions, keys present, Ollama liveness
```

`~/.oknoll/` (relocatable via `OKNOLL_HOME`) holds two files, split like
`~/.aws`: `config.toml` — machine defaults, no secrets, safe to share — and
`.env` (`chmod 600`) for keys shared by every bundle on the machine (see
`.env.example`). A bundle whose `oknoll.toml` sets `[build].model` explicitly
keeps that choice regardless of the machine default; `oknoll system eval` never
inherits settings from config files (its `--model`/`--embedder` default to the
stub so benchmark comparisons stay reproducible).

Set `GITHUB_TOKEN` before `oknoll project build` to raise GitHub API rate limits for
larger repositories. `auth login` lands with the hosted control plane in a later phase
and currently exits with a clear "not implemented yet" message.

### Regenerating model output

Model generations are cached by content hash + prompt version + model id, so
rebuilds are reproducible: `oknoll bundle diff --check` proves a rebuild matches the
published revision byte for byte. The flip side is that cached output never
improves on its own. Regeneration is therefore a deliberate, versioned event —
bump the knob in `oknoll.toml` and rebuild:

```toml
[build]
generation_version = "1"   # bump to re-make every cached model decision
```

The next `oknoll project build` re-asks the model for every generated field (concept
plans and descriptions) and, if anything changed, publishes a new immutable
revision with a reviewable diff — the old revision stays untouched. Commit the
bump alongside the new revision so the regeneration is visible in history.
Switching models needs no bump: a new model id already invalidates the cache.

With the Anthropic provider, a request declined by a safety classifier is
automatically re-run on Anthropic's recommended fallback model in the same
call instead of failing the build (`oknoll project build` prints a note when this
happens). The cache stores which model actually served each generation, so
provenance stays honest even when the fallback answers.

## What's in this repo

Sources are normalized into a format-neutral **CanonicalDoc** model, then serialized as
**OKF v0.2** (Open Knowledge Format) bundles — plain Markdown trees with frontmatter and
a deterministic manifest. OKF's posture is strict producer, permissive consumer: this
tooling emits strictly valid bundles, but tolerates unknown types and keys in bundles
produced by others.

| Path | Contents |
|---|---|
| `packages/okf-core` | CanonicalDoc, OKF v0.2 parser/writer, five-level lint, pipeline, indexes, packer |
| `packages/oknoll-cli` | `oknoll` Typer CLI (resource-namespaced command surface) |
| `packages/oknoll-runtime` | local runtime: content-addressed store, OkNoll OCI images, catalog, locator grammar |
| `packages/connectors` | files/web/github/transcript connectors + plugin SDK |
| `packages/eval` | PD-vs-RAG evaluation harness |
| `fixtures/` | golden bundles and malformed cases |

This repo is fully standalone with no cloud dependency; the hosted OpenKnoll
platform consumes these packages as pinned git-tag dependencies.

## Development

Requires [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is provisioned automatically).

```sh
make install    # uv sync --all-packages
make lint       # ruff format --check + ruff check
make typecheck  # mypy (strict)
make test       # pytest across the workspace
uv run oknoll --help
```

Branches: feature branches off `develop`; `main` is promotion-only.

Releasing (all six packages move in lockstep with the tag):

```sh
uv run python scripts/bump_version.py 0.4.0   # pyprojects, sibling pins, __version__
make install && make lint typecheck test      # refresh uv.lock, verify
# commit, promote develop → main by PR, then:
git tag v0.4.0 && git push origin v0.4.0      # release.yml gates, builds, publishes
```

After publishing to PyPI, `release.yml` regenerates the Homebrew formula
(`scripts/generate_brew_formula.py` — resolves the released version's full
dependency closure and emits the formula; `brew update-python-resources`
can't be used because it ignores packages uploaded within the last day) and
opens a PR against [openknoll/homebrew-tap](https://github.com/openknoll/homebrew-tap).
The tap's own `brew test-bot` CI gates that PR (audit, source install,
`brew test`); merge it with the tap's `brew pr-pull` workflow so bottles get
built and published with it.

Troubleshooting (macOS): if a long-lived `oknoll` process ever dies on import with
`ModuleNotFoundError: No module named 'okf_core'`, run `chflags -R nohidden .venv` once —
a historical macOS hidden-flag issue (Python 3.12+ skips hidden `.pth` files), believed
resolved by keeping the checkout out of iCloud-synced paths.

## License

Apache-2.0.

# OpenKnoll — Python core + CLI (`oknoll-python`)

Local-first knowledge packaging: ingest documents, websites, and repositories; normalize them
into a format-neutral **CanonicalDoc** model; serialize portable **OKF v0.2** Markdown bundles;
and explore them through a deterministic navigation layer (CLI, web, MCP). The file is the
contract — a valid bundle stays useful without an OpenKnoll account, cloud service, or
proprietary reader.

## Layout

| Path | Contents |
|---|---|
| `packages/okf-core` | CanonicalDoc, OKF v0.2 parser/writer, five-level lint, pipeline, indexes, packer |
| `packages/oknoll-cli` | `oknoll` Typer CLI (frozen command surface) |
| `packages/connectors` | files/web/github/transcript connectors + plugin SDK |
| `packages/eval` | PD-vs-RAG evaluation harness |
| `fixtures/` | golden bundles and malformed cases |

This repo is fully standalone with no cloud dependency; the hosted OpenKnoll
platform consumes these packages as pinned git-tag dependencies.

## Install

The `oknoll` CLI is fully useful offline and local — no cloud account, no service.
From PyPI (v0.3.0 onward):

```sh
uv tool install oknoll     # or: pipx install oknoll
oknoll --help
```

To hack on it instead, run it out of a clone with [`uv`](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/openknoll/oknoll-python.git
cd oknoll-python && make install     # one-time: sync the uv workspace

# `--project` lets you invoke the CLI from any directory in any later session:
REPO="$PWD"
oknoll() { uv run --no-sync --project "$REPO" oknoll "$@"; }
```

## Usage

### Demo: documents + a GitHub repo → portable OKF bundle

```sh
# 1. Create a project (config, ignore file, bundle skeleton).
mkdir -p ~/demo && cd ~/demo
oknoll init handbook-demo --name "Team Handbook"
cd handbook-demo

# 2. Register sources. Local files/dirs (md, txt, PDF, docx) are probed on the spot;
#    GitHub repos and websites are registered and fetched at build time.
cp -R "$REPO/fixtures/sources/handbook" sources/handbook   # or use your own docs
oknoll add sources/handbook
oknoll add https://github.com/openknoll/oknoll-python      # commit-SHA provenance
# oknoll add https://your-site.example/docs/               # sitemap-first same-site
                                                           # crawl, robots honored,
                                                           # 100-page cap

# 3. Build: acquire → normalize → plan → generate → link → lint → index → publish.
oknoll build
# 6 concept(s) from 6 source file(s); cache 0 hit(s), 12 miss(es)
# published revision rev-a4d583b7229c

# 4. The bundle is plain Markdown + a manifest — the file is the contract.
ls bundle/concepts/            # one concept per source document
head bundle/references/source-006.md   # provenance: connector, uri (pinned to the
                                       # exact commit SHA for repos), source hash

# 5. Verify. Rebuilds are idempotent (content-derived revision ids, build cache):
oknoll build                   # cache 12 hit(s) → "no changes"
oknoll lint bundle --strict    # five-level validation; 0 findings
oknoll diff --check            # rebuild to temp, byte-compare — CI reproducibility gate

# 6. Ask a question. The explorer navigates deterministically — overview → search →
#    peek → read → follow links — and answers only from what it retrieved.
oknoll ask "How are credentials stored?"
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
oknoll ask "What is the Zanzibar revenue forecast?"
# Insufficient evidence in this bundle to answer. No concept mentions: forecast,
# revenue, zanzibar. Adding a source about … would resolve the gap.

oknoll ask "…" --json          # answer + citations + warnings + full trace, machine-readable

# 7. Pack a deterministic, checksummed archive for sharing.
oknoll pack                    # dist/Team Handbook-rev-….tar.gz  + .sha256
oknoll pack --profile plain    # same tree with OKF-only frontmatter keys stripped
```

### Serve a bundle to an agent over MCP

`oknoll serve --mcp` exposes the bundle to any [MCP](https://modelcontextprotocol.io)
client as a read-only stdio server. It offers exactly the seven deterministic explorer
tools — `overview`, `list`, `search`, `peek`, `read`, `links`, `history` — each with a
JSON schema, bounded output, and path validation. The server adds no capability the local
explorer does not already have: it cannot write, follow a link off disk, reach the
network, or expose anything outside the bundle root. Bundle text stays data.

```sh
oknoll serve --mcp                       # serves the active project's bundle
oknoll serve --mcp --bundle /path/to/any/bundle   # or any bundle, incl. someone else's
```

It speaks JSON-RPC on stdout and prints status to stderr, so it is driven by an MCP
client, not by hand.

#### Connect it to a local agent (Claude Code)

Register the server once, pointing it at a built bundle (use absolute paths):

```sh
claude mcp add oknoll -- \
  uv run --no-sync --project /abs/path/to/oknoll-python \
  oknoll serve --mcp --bundle /abs/path/to/handbook-demo/bundle
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
               "oknoll", "serve", "--mcp", "--bundle", "/abs/path/to/bundle"]
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
`run --no-sync --project /abs/path/to/oknoll-python oknoll serve --mcp --bundle /abs/path/to/bundle`,
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

`oknoll chat` is a single-bundle REPL over the same explorer, pinned to the current
revision. Conversations persist under `bundle/.oknoll/conversations/` (derived state,
never packed) and resume by id:

```sh
oknoll chat                    # ask, ask again; `exit` to quit
oknoll chat --mode rag         # vector baseline instead of progressive disclosure
oknoll chat --resume chat-20260807-….   # continue where you left off
```

`oknoll plugin list|inspect|validate` reports the installed connectors and checks them
against the connector protocol.

Revisions are immutable under `bundle/.oknoll/revisions/`; the bundle top level always
materializes the current revision. Every `ask` writes a separate trace record
(tools called, paths read, characters and tokens spent, latency, model, revision id,
retrieval condition) under `bundle/.oknoll/traces/` — derived state, never packed.
Bundle text is data: nothing a bundle says can make the explorer follow a link off
disk, reach the network, or drop a trust warning.

### Configuration

By default `build`/`ask`/`chat` use a deterministic stub model (no network,
reproducible). Real providers are configured in two separate planes:

- **Settings** (which model/embedder, the Ollama endpoint) resolve
  *specific beats general*: CLI flag (`ask --model …`) → the bundle's
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
oknoll doctor           # home dir, .env permissions, keys present, Ollama liveness
```

`~/.oknoll/` (relocatable via `OKNOLL_HOME`) holds two files, split like
`~/.aws`: `config.toml` — machine defaults, no secrets, safe to share — and
`.env` (`chmod 600`) for keys shared by every bundle on the machine (see
`.env.example`). A bundle whose `oknoll.toml` sets `[build].model` explicitly
keeps that choice regardless of the machine default; `oknoll eval` never
inherits settings from config files (its `--model`/`--embedder` default to the
stub so benchmark comparisons stay reproducible).

Set `GITHUB_TOKEN` before `oknoll build` to raise GitHub API rate limits for larger
repositories. `login` and `keys` land with the hosted control plane in a later phase and
currently exit with a clear "not implemented yet" message.

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

Releasing (all five packages move in lockstep with the tag):

```sh
uv run python scripts/bump_version.py 0.4.0   # pyprojects, sibling pins, __version__
make install && make lint typecheck test      # refresh uv.lock, verify
# commit, promote develop → main by PR, then:
git tag v0.4.0 && git push origin v0.4.0      # release.yml gates, builds, publishes
```

Troubleshooting (macOS): if a long-lived `oknoll` process ever dies on import with
`ModuleNotFoundError: No module named 'okf_core'`, run `chflags -R nohidden .venv` once —
a historical macOS hidden-flag issue (Python 3.12+ skips hidden `.pth` files), believed
resolved by keeping the checkout out of iCloud-synced paths.

## License

Apache-2.0.

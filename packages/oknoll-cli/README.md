# oknoll

Local-first CLI for building, linting, exploring, and serving **OKF v0.2**
knowledge bundles — plain-Markdown, citable, deterministic. Includes the
local content store, the multi-bundle daemon (web UI, HTTP API, and MCP
Streamable HTTP behind one loopback endpoint), and a stdio MCP server.

```sh
uv tool install oknoll        # or: pipx install oknoll
oknoll project init myproject && cd myproject
oknoll source add ./docs && oknoll project build
oknoll query ask "what does the security policy say?"   # cited answer
oknoll bundle install ./bundle --name handbook          # local content store
oknoll daemon start && oknoll mcp config --client claude
```

## Upgrading from v0.3

v0.4.0 replaced the flat command surface with resource namespaces in one
clean break. Every old command prints its replacement and exits 2:

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

Part of [OpenKnoll](https://github.com/openknoll/oknoll-python) — full
documentation, the OKF format, and the design live in the repository README.

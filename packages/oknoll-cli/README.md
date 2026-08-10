# oknoll

Local-first CLI for building, linting, exploring, and packing **OKF v0.2**
knowledge bundles — plain-Markdown, citable, deterministic. Includes the stdio
MCP server (`oknoll serve --mcp`) so agents can explore a bundle.

```sh
uv tool install oknoll        # or: pipx install oknoll
oknoll init myproject && cd myproject
oknoll add ./docs && oknoll build
oknoll ask "what does the security policy say?"
```

Part of [OpenKnoll](https://github.com/openknoll/oknoll-python) — full
documentation, the OKF format, and the design live in the repository README.

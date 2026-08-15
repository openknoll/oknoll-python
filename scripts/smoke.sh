#!/usr/bin/env bash
# smoke.sh — local CLI smoke checks over the v0.4 namespace surface:
# project init → source add → project build → bundle lint → query ask →
# query chat → system plugins → MCP, over a fixture project. Also proves the
# flat v0.3 commands are pointer stubs. No network, no cloud.
set -euo pipefail

if [[ $# -ge 1 ]]; then
  echo "smoke: this script only smokes the local CLI (no arguments supported)" >&2
  exit 1
fi

echo "smoke: local CLI checks"
uv run --no-sync oknoll --help >/dev/null
uv run --no-sync oknoll bundle lint fixtures/bundles/golden/minimal >/dev/null

# The v0.3 flat surface is pointer-only: exit 2 + the replacement, no work.
# Capture first — the stub's deliberate exit 2 would trip pipefail in a pipeline.
legacy_rc=0
legacy_out="$(uv run --no-sync oknoll build 2>&1)" || legacy_rc=$?
if [[ "$legacy_rc" -ne 2 ]]; then
  echo "smoke: legacy 'oknoll build' should have exited 2 (got $legacy_rc)" >&2
  exit 1
fi
echo "$legacy_out" | grep -q "moved: use 'oknoll project build'"

# Files → build → deterministic archive, reproducible via bundle diff --check;
# then ask over the built revision, with citations and a written trace.
workdir="$(mktemp -d)"
# Store trees are published write-protected; unprotect before removing.
# Best-effort daemon stop first: the run record lives under the temp home.
trap 'uv run --no-sync oknoll daemon stop >/dev/null 2>&1 || true; \
  chmod -R u+w "$workdir" 2>/dev/null || true; rm -rf "$workdir"' EXIT
repo="$(pwd)"
# Hermetic: config, secrets, and the runtime store/catalog all live under the
# temp dir — the smoke never reads ~/.oknoll or writes the real content store.
export OKNOLL_HOME="$workdir/home"
uv run --no-sync oknoll project init "$workdir/proj" --name smoke >/dev/null
cp -R "$repo/fixtures/sources/handbook" "$workdir/proj/sources/handbook"
(
  cd "$workdir/proj"
  uv run --no-sync --project "$repo" oknoll source add sources/handbook >/dev/null
  uv run --no-sync --project "$repo" oknoll project build >/dev/null
  uv run --no-sync --project "$repo" oknoll bundle diff --check >/dev/null
  uv run --no-sync --project "$repo" oknoll bundle pack >/dev/null
  uv run --no-sync --project "$repo" oknoll bundle pack --profile plain >/dev/null

  answer="$(uv run --no-sync --project "$repo" oknoll query ask "How are credentials stored?" --json)"
  echo "$answer" | grep -q '"abstained": false'
  echo "$answer" | grep -q 'concepts/security-policy.md'
  echo "$answer" | grep -q '"condition": "pd"'
  test -n "$(find bundle/.oknoll/traces -name 'ask-*.json' -print -quit)"

  # An unanswerable question abstains instead of inventing an answer.
  uv run --no-sync --project "$repo" oknoll query ask "What is the Zanzibar forecast?" --json \
    | grep -q '"abstained": true'

  # Single-bundle chat persists a revision-pinned conversation.
  # Capture first — a `| grep -q` would SIGPIPE chat and trip pipefail.
  chatlog="$(printf 'How are credentials stored?\nexit\n' \
    | uv run --no-sync --project "$repo" oknoll query chat)"
  echo "$chatlog" | grep -q 'concepts/security-policy.md'
  test -n "$(find bundle/.oknoll/conversations -name 'chat-*.jsonl' -print -quit)"

  # Plugin surface over the first-party connectors.
  uv run --no-sync --project "$repo" oknoll system plugins list | grep -q files
  uv run --no-sync --project "$repo" oknoll system plugins validate github | grep -q conforms
)

# Phase 16 store surface: two bundles installed (golden + foreign) → catalog →
# cited answer over local:, checkout, image save/load round trip, and the
# safe-extraction gate rejecting a malicious archive.
uv run --no-sync oknoll bundle install fixtures/bundles/golden/multihop --name handbook >/dev/null
uv run --no-sync oknoll bundle install fixtures/bundles/upstream/acme_retail --name acme >/dev/null
uv run --no-sync oknoll bundle list | grep -q handbook
uv run --no-sync oknoll bundle list | grep -q acme
localask="$(uv run --no-sync oknoll query ask local:handbook \
  "Who must sign off a production release?" --json)"
echo "$localask" | grep -q '"abstained": false'
echo "$localask" | grep -q '"bundle": "handbook@sha256:'
uv run --no-sync oknoll bundle checkout local:handbook "$workdir/checkout" >/dev/null
uv run --no-sync oknoll image build fixtures/bundles/golden/minimal --tag minimal:1.0 >/dev/null
uv run --no-sync oknoll image save minimal:1.0 -o "$workdir/minimal.tar" >/dev/null
uv run --no-sync oknoll image load "$workdir/minimal.tar" >/dev/null
# Capture first — the deliberate exit 1 would otherwise stop the script.
unpack_rc=0
uv run --no-sync oknoll bundle unpack fixtures/security/archives/traversal.tar \
  "$workdir/evil" 2>/dev/null || unpack_rc=$?
if [[ "$unpack_rc" -ne 1 || -e "$workdir/evil" ]]; then
  echo "smoke: malicious archive should have been rejected (got $unpack_rc)" >&2
  exit 1
fi

# Phase 17 daemon surface: one loopback origin serving UI + API + MCP for
# every installed bundle. Health-checked start, token-gated API, origin
# checks, multi-bundle chat with qualified identities, clean stop.
port="$(uv run --no-sync python -c \
  'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
uv run --no-sync oknoll daemon start --port "$port" >/dev/null
uv run --no-sync oknoll daemon status --json | grep -q '"status": "ok"'
uv run --no-sync oknoll mcp endpoint | grep -q "/mcp"
uv run --no-sync oknoll mcp config --client claude | grep -q '"Authorization": "Bearer '
uv run --no-sync oknoll ui open --print | grep -q '#token='
uv run --no-sync python - "$port" <<'PY'
import sys
import httpx
from oknoll_runtime import runtime_dirs
from oknoll_runtime.daemon import read_token

port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
token = read_token(runtime_dirs().state)
assert token, "daemon token missing"
assert httpx.get(f"{base}/api/v1/catalog", timeout=5.0).status_code == 401
assert (
    httpx.get(
        f"{base}/api/v1/catalog",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://evil.example"},
        timeout=5.0,
    ).status_code
    == 403
)
catalog = httpx.get(
    f"{base}/api/v1/catalog", headers={"Authorization": f"Bearer {token}"}, timeout=5.0
)
assert catalog.status_code == 200
aliases = [b["alias"] for b in catalog.json()["bundles"]]
assert "handbook" in aliases and "acme" in aliases, aliases
assert httpx.get(f"{base}/", timeout=5.0).status_code == 200
PY
daemonchat="$(printf 'Who must sign off a production release?\n@acme What is the return policy?\nexit\n' \
  | uv run --no-sync oknoll query chat --bundle handbook --bundle acme)"
echo "$daemonchat" | grep -q 'handbook@sha256:'
echo "$daemonchat" | grep -q 'acme@sha256:'
uv run --no-sync oknoll daemon stop >/dev/null

# The local stdio MCP server answers the real protocol over a spawned
# subprocess — list the seven tools and call one, no model involved.
uv run --no-sync --project "$repo" python "$repo/scripts/mcp_smoke.py" \
  "$repo/fixtures/bundles/golden/multihop"
echo "smoke: OK"

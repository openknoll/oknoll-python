#!/usr/bin/env bash
# smoke.sh BASE_URL — API → build → chat → MCP → share → revoke → export checks (design §16.2).
# Phase 7 wires this against the deployed dev environment. Until then it only
# exercises the local CLI surface so `make smoke` stays honest.
set -euo pipefail

if [[ $# -ge 1 ]]; then
  echo "smoke: cloud checks against $1 are not implemented until Phase 7" >&2
  exit 1
fi

echo "smoke: local CLI checks"
uv run --no-sync oknoll --help >/dev/null
uv run --no-sync oknoll lint fixtures/bundles/golden/minimal >/dev/null

# Phase 2 flow: files → build → deterministic archive, reproducible via diff --check.
# Phase 4 adds: ask over the built revision, with citations and a written trace.
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
repo="$(pwd)"
uv run --no-sync oknoll init "$workdir/proj" --name smoke >/dev/null
cp -R "$repo/fixtures/sources/handbook" "$workdir/proj/sources/handbook"
(
  cd "$workdir/proj"
  uv run --no-sync --project "$repo" oknoll add sources/handbook >/dev/null
  uv run --no-sync --project "$repo" oknoll build >/dev/null
  uv run --no-sync --project "$repo" oknoll diff --check >/dev/null
  uv run --no-sync --project "$repo" oknoll pack >/dev/null
  uv run --no-sync --project "$repo" oknoll pack --profile plain >/dev/null

  answer="$(uv run --no-sync --project "$repo" oknoll ask "How are credentials stored?" --json)"
  echo "$answer" | grep -q '"abstained": false'
  echo "$answer" | grep -q 'concepts/security-policy.md'
  echo "$answer" | grep -q '"condition": "pd"'
  test -n "$(find bundle/.oknoll/traces -name 'ask-*.json' -print -quit)"

  # An unanswerable question abstains instead of inventing an answer.
  uv run --no-sync --project "$repo" oknoll ask "What is the Zanzibar forecast?" --json \
    | grep -q '"abstained": true'

  # Phase 7: single-bundle chat persists a revision-pinned conversation.
  # Capture first — a `| grep -q` would SIGPIPE chat and trip pipefail.
  chatlog="$(printf 'How are credentials stored?\nexit\n' \
    | uv run --no-sync --project "$repo" oknoll chat)"
  echo "$chatlog" | grep -q 'concepts/security-policy.md'
  test -n "$(find bundle/.oknoll/conversations -name 'chat-*.jsonl' -print -quit)"

  # Phase 7: plugin surface over the first-party connectors.
  uv run --no-sync --project "$repo" oknoll plugin list | grep -q files
  uv run --no-sync --project "$repo" oknoll plugin validate github | grep -q conforms
)

# Phase 7: the local stdio MCP server answers the real protocol over a spawned
# subprocess (MCP-1/2) — list the seven tools and call one, no model involved.
uv run --no-sync --project "$repo" python "$repo/scripts/mcp_smoke.py" \
  "$repo/fixtures/bundles/golden/multihop"
echo "smoke: OK"

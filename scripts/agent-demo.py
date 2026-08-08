#!/usr/bin/env python3
"""Phase 7 exit gate: an independent LLM agent answers a multi-hop question
through `oknoll serve --mcp` (design §10 MCP-1/2, implementation plan Phase 7).

This client is deliberately independent of OpenKnoll internals: it reaches the
bundle only through the MCP stdio protocol (a spawned `oknoll serve --mcp`
subprocess) and the model only through the Anthropic API — the same position
any external agent client is in. Citations come from the model's answer;
the inspectable trace is the tool-call log this script prints as it happens.

Usage (needs ANTHROPIC_API_KEY; a .env in the current directory is honored):

    uv run --no-sync python scripts/agent-demo.py
    uv run --no-sync python scripts/agent-demo.py --bundle PATH --question "..."

With no --bundle, a temp copy of fixtures/bundles/golden/multihop is served —
the frozen multi-hop fixture whose answer (concepts/duty-roster.md) is never
surfaced by lexical search and is reachable only by following a link from
concepts/release-process.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[1]
DEFAULT_QUESTION = "Who must sign off a production release?"
DEFAULT_MODEL = "claude-opus-5"
MAX_TURNS = 16

SYSTEM = """\
You answer questions about one knowledge bundle using only its MCP tools.
Navigate progressively: overview first, then search or list, peek before read,
and follow links when the evidence points elsewhere. Answer only from what you
retrieved, cite the bundle paths you used in the answer text, and say so if the
bundle cannot answer.\
"""


# Only the demo's own credential is read from a project .env. An allowlist
# matters here because this script spawns `oknoll serve --mcp` as a subprocess
# that inherits the environment, and the expected workflow is to run the demo
# from inside an arbitrary (possibly foreign) bundle directory — an unrestricted
# loader would let that directory's .env inject PYTHONPATH, HTTPS_PROXY,
# ANTHROPIC_BASE_URL, etc. into the child.
_DOTENV_ALLOWLIST = frozenset({"ANTHROPIC_API_KEY"})


def load_dotenv(path: Path) -> None:
    """Load allowlisted keys from a project .env (setdefault — the shell wins)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in _DOTENV_ALLOWLIST:
            continue
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


async def run_agent(bundle: Path, question: str, model: str) -> int:
    import anthropic

    transport = stdio_client(
        StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--no-sync",
                "--project",
                str(REPO),
                "oknoll",
                "serve",
                "--mcp",
                "--bundle",
                str(bundle),
            ],
        )
    )
    async with Client(transport) as mcp_client:
        info = mcp_client.server_info
        print(f"server : {info.name} {info.version}" if info else "server : (unidentified)")
        listed = (await mcp_client.list_tools()).tools
        print(f"tools  : {', '.join(t.name for t in listed)}")
        print(f"bundle : {bundle}")
        print(f"question: {question}\n")

        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in listed
        ]
        client = anthropic.Anthropic()
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

        for _ in range(MAX_TURNS):
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=SYSTEM,
                tools=tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                answer = "".join(b.text for b in response.content if b.type == "text")
                print("\n--- answer " + "-" * 45)
                print(answer.strip() or "(model returned no text)")
                if response.stop_reason == "max_tokens":
                    print("\n[warning] answer truncated at max_tokens", file=sys.stderr)
                    return 1
                return 0

            results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = await mcp_client.call_tool(block.name, dict(block.input))  # type: ignore[arg-type]
                payload = (
                    json.dumps(result.structured_content)
                    if result.structured_content is not None
                    else "".join(str(getattr(c, "text", "")) for c in result.content)
                )
                flag = " [error]" if result.is_error else ""
                print(f"→ {block.name} {json.dumps(block.input)} ({len(payload)} chars){flag}")
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": payload,
                        "is_error": bool(result.is_error),
                    }
                )
            messages.append({"role": "user", "content": results})

        print("agent did not finish within the turn budget", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=None, help="Bundle directory to serve.")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    load_dotenv(Path.cwd() / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (env or ./.env)", file=sys.stderr)
        return 1

    if args.bundle is not None:
        return int(anyio.run(run_agent, args.bundle.resolve(), args.question, args.model))

    # Serve a temp copy — exploring writes derived state under .oknoll/,
    # which must never land in the frozen fixture tree.
    with tempfile.TemporaryDirectory(prefix="oknoll-agent-demo-") as tmp:
        bundle = Path(tmp) / "multihop"
        shutil.copytree(REPO / "fixtures" / "bundles" / "golden" / "multihop", bundle)
        return int(anyio.run(run_agent, bundle, args.question, args.model))


if __name__ == "__main__":
    raise SystemExit(main())

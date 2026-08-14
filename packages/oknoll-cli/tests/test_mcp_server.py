"""MCP contract + security tests for `oknoll mcp stdio`.

Everything runs over an in-memory client/server session — the same protocol
layer a real stdio client exercises, minus the pipes. The contract pinned here:

- exactly the seven explorer tools, all read-only, each with an input schema;
- bounded outputs (the explorer guardrails survive the protocol hop);
- guardrail refusals surface as *structured tool errors*, never protocol
  failures or tracebacks;
- hostile bundle text is served as quoted data and cannot escape the bundle.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.types import CallToolResult
from okf_core.explorer import MAX_LIST_LIMIT, MAX_PEEK_CHARS, MAX_SEARCH_LIMIT, TOOLS
from oknoll_cli.mcp_server import build_server

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"
HOSTILE = REPO_ROOT / "fixtures" / "security" / "prompt-injection" / "bundle"

SEVEN = set(TOOLS)


@pytest.fixture()
def minimal(tmp_path: Path) -> Path:
    dest = tmp_path / "minimal"
    shutil.copytree(GOLDEN / "minimal", dest)
    return dest


@pytest.fixture()
def multihop(tmp_path: Path) -> Path:
    dest = tmp_path / "multihop"
    shutil.copytree(GOLDEN / "multihop", dest)
    return dest


@pytest.fixture()
def hostile(tmp_path: Path) -> Path:
    dest = tmp_path / "hostile"
    shutil.copytree(HOSTILE, dest)
    return dest


def _call(bundle: Path, tool: str, args: dict[str, Any] | None = None) -> CallToolResult:
    async def go() -> CallToolResult:
        async with Client(build_server(bundle, today="2026-08-06")) as client:
            return await client.call_tool(tool, args or {})

    return anyio.run(go)


def _payload(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error is False, [getattr(c, "text", c) for c in result.content]
    if result.structured_content is not None:
        assert isinstance(result.structured_content, dict)
        return result.structured_content
    text = getattr(result.content[0], "text", None)
    assert isinstance(text, str)
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _error_text(result: CallToolResult) -> str:
    assert result.is_error is True
    return " ".join(str(getattr(c, "text", "")) for c in result.content)


# -- MCP-1: the seven tools, schemas, read-only ------------------------------


def test_tool_surface_is_the_seven_design_tools(minimal: Path) -> None:
    async def go() -> None:
        async with Client(build_server(minimal)) as client:
            listed = await client.list_tools()
            tools = {t.name: t for t in listed.tools}
            assert set(tools) == SEVEN
            for name, tool in tools.items():
                assert tool.description, name
                schema = tool.input_schema
                assert schema["type"] == "object", name
                assert tool.annotations is not None, name
                assert tool.annotations.read_only_hint is True, name
                assert tool.annotations.destructive_hint is False, name
            # path-taking tools require the path in the schema, not just at runtime
            for name in ("peek", "read", "links"):
                assert "path" in tools[name].input_schema.get("required", []), name
            assert "query" in tools["search"].input_schema["required"]

    anyio.run(go)


def test_server_refuses_a_missing_bundle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bundle directory not found"):
        build_server(tmp_path / "nope")


# -- MCP-2: bounded outputs over the protocol --------------------------------


def test_overview_and_history_round_trip(minimal: Path) -> None:
    overview = _payload(_call(minimal, "overview"))
    assert overview["title"] == "Minimal golden bundle"
    assert overview["counts"] == {"concepts": 2, "references": 1}

    history = _payload(_call(minimal, "history"))
    assert "current_revision_id" in history
    assert isinstance(history["entries"], list)


def test_list_is_bounded_and_metadata_only(minimal: Path) -> None:
    listing = _payload(_call(minimal, "list", {"directory": "concepts", "limit": 1}))
    assert listing["total"] == 2
    assert len(listing["entries"]) == 1
    assert all("body" not in e for e in listing["entries"])

    clamped = _payload(_call(minimal, "list", {"limit": 10_000}))
    assert len(clamped["entries"]) <= MAX_LIST_LIMIT


def test_search_returns_snippets_never_bodies(minimal: Path) -> None:
    found = _payload(_call(minimal, "search", {"query": "authentication", "limit": 9_999}))
    assert found["results"], "expected lexical hits"
    assert len(found["results"]) <= MAX_SEARCH_LIMIT
    assert all(len(str(r["snippet"])) < 400 for r in found["results"])


def test_peek_then_read_with_caps(minimal: Path) -> None:
    peeked = _payload(_call(minimal, "peek", {"path": "concepts/architecture.md", "lines": 2}))
    assert peeked["truncated"] is True
    assert len(str(peeked["body_start"])) <= MAX_PEEK_CHARS

    read = _payload(_call(minimal, "read", {"path": "concepts/architecture.md", "max_chars": 20}))
    assert read["truncated"] is True
    assert len(str(read["body"])) == 20


def test_links_reports_graph_edges(multihop: Path) -> None:
    linked = _payload(_call(multihop, "links", {"path": "concepts/release-process.md"}))
    assert "concepts/duty-roster.md" in linked["outbound"]


# -- MCP-2: structured errors, path validation -------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "/etc/passwd",
        "concepts/../../escape.md",
        ".oknoll/cache/build-cache.json",
        "concepts/does-not-exist.md",
        "manifest.json",  # real file, but not markdown
        "",
    ],
)
def test_bad_paths_become_structured_tool_errors(minimal: Path, path: str) -> None:
    for tool in ("peek", "read", "links"):
        result = _call(minimal, tool, {"path": path})
        message = _error_text(result)
        assert tool in message  # the refusal names the tool…
        assert "Traceback" not in message  # …and is one line, not a stack dump


def test_tool_errors_do_not_leak_host_paths(minimal: Path, tmp_path: Path) -> None:
    """An OSError mid-session (a file deleted after the map was cached) must
    surface a generic refusal, never the absolute host path."""

    async def go() -> None:
        async with Client(build_server(minimal, today="2026-08-06")) as client:
            # populate the cached file map, then delete the file out from under it
            assert (
                await client.call_tool("peek", {"path": "concepts/architecture.md"})
            ).is_error is False
            (minimal / "concepts" / "architecture.md").unlink()
            result = await client.call_tool("read", {"path": "concepts/architecture.md"})
            text = " ".join(str(getattr(c, "text", "")) for c in result.content)
            assert result.is_error is True
            assert "cannot serve this request" in text
            assert str(minimal) not in text  # no absolute host path
            assert "/private" not in text and "/Users" not in text

    anyio.run(go)


def test_bad_arguments_are_schema_errors_not_crashes(minimal: Path) -> None:
    missing = _call(minimal, "read", {})
    text = _error_text(missing)
    # a schema rejection names the field, not a stack dump
    assert "path" in text.lower()
    assert "Traceback" not in text

    bad_direction = _call(minimal, "links", {"path": "index.md", "direction": "sideways"})
    assert "inbound|outbound" in _error_text(bad_direction)


# -- prompt injection: hostile bundle text stays data ------------------------


def test_hostile_bundle_is_served_as_quoted_data(hostile: Path) -> None:
    async def go() -> None:
        async with Client(build_server(hostile)) as client:
            before = {t.name for t in (await client.list_tools()).tools}
            assert before == SEVEN

            read = await client.call_tool("read", {"path": "concepts/instruction-override.md"})
            assert read.is_error is False  # hostile prose is evidence, not an error…
            body = str((read.structured_content or {}).get("body", ""))
            assert "ignore" in body.lower()  # …returned verbatim as data

            # reading hostile text must not grow, rename, or drop tools
            after = {t.name for t in (await client.list_tools()).tools}
            assert after == SEVEN

    anyio.run(go)


def test_hostile_links_never_resolve_outside_the_bundle(hostile: Path) -> None:
    read = _payload(_call(hostile, "read", {"path": "concepts/traversal-links.md"}))
    for link in read["links"]:
        resolved = link["resolved"]
        if resolved is not None:
            assert not str(resolved).startswith(("/", ".."))
    # and the graph itself contains no edge escaping the root
    linked = _payload(_call(hostile, "links", {"path": "concepts/traversal-links.md"}))
    for edge in linked["outbound"]:
        assert not str(edge).startswith(("/", ".."))

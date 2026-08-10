"""Graph projection and self-contained HTML visualization."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from okf_core.viz import build_graph, render_html, write_viz


def _graph(golden_dir: Path) -> dict[str, Any]:
    return build_graph(golden_dir / "multihop", today="2026-08-09")


def test_nodes_are_concepts_and_references_only(golden_dir: Path) -> None:
    graph = _graph(golden_dir)
    paths = {n["path"] for n in graph["nodes"]}
    assert paths == {
        "concepts/duty-roster.md",
        "concepts/incident-response.md",
        "concepts/release-process.md",
        "references/source-001.md",
    }
    kinds = {n["path"]: n["kind"] for n in graph["nodes"]}
    assert kinds["references/source-001.md"] == "reference"
    assert kinds["concepts/duty-roster.md"] == "concept"


def test_root_absolute_links_become_edges(golden_dir: Path) -> None:
    graph = _graph(golden_dir)
    pairs = {(e["source"], e["target"]) for e in graph["edges"]}
    # Concept↔concept prose links and concept→reference citation links, all
    # written in the bundle-root-absolute form the OKF spec recommends.
    assert ("concepts/release-process.md", "concepts/duty-roster.md") in pairs
    assert ("concepts/duty-roster.md", "concepts/incident-response.md") in pairs
    assert ("concepts/release-process.md", "references/source-001.md") in pairs
    # index.md is not a node, so its hub links contribute no edges.
    assert not any("index.md" in p for pair in pairs for p in pair)


def test_node_metadata_projection(golden_dir: Path) -> None:
    graph = _graph(golden_dir)
    by_path = {n["path"]: n for n in graph["nodes"]}
    release = by_path["concepts/release-process.md"]
    assert release["title"] == "Release process"
    assert release["type"] == "Reference"
    assert release["trust_tier"] == "human-reviewed"
    assert release["stale"] is False
    # Frontmatter-less reference snapshots still project as titled nodes.
    source = by_path["references/source-001.md"]
    assert source["title"] == "source-001"
    assert source["trust_tier"] == "unverified"


def test_build_graph_is_deterministic(golden_dir: Path) -> None:
    first = json.dumps(_graph(golden_dir), sort_keys=True)
    second = json.dumps(_graph(golden_dir), sort_keys=True)
    assert first == second


def test_render_html_escapes_script_breakout() -> None:
    graph: dict[str, Any] = {
        "title": "x",
        "revision_id": None,
        "nodes": [
            {
                "path": "concepts/a.md",
                "kind": "concept",
                "title": "</script><script>alert(1)</script>",
                "type": None,
                "description": None,
                "status": None,
                "tags": [],
                "trust_tier": "unverified",
                "stale": False,
                "chars": 1,
            }
        ],
        "edges": [],
    }
    html = render_html(graph)
    # Bundle text is untrusted: it must not be able to close the JSON block.
    payload = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    assert payload is not None
    assert "</script>" not in payload.group(1)


def test_write_viz_writes_offline_html(golden_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "viz.html"
    stats = write_viz(golden_dir / "multihop", out, today="2026-08-09")
    assert stats["nodes"] == 4
    assert stats["edges"] >= 5
    html = out.read_text(encoding="utf-8")
    assert stats["bytes"] == len(html.encode("utf-8"))
    # Self-contained: no external fetches of any kind.
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in html
    assert "@import" not in html and "src=" not in html

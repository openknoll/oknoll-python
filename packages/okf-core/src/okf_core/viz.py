"""Bundle link-graph projection and a self-contained HTML visualization.

``build_graph`` projects a bundle tree into a JSON-safe nodes/edges dict —
concepts and references are nodes, resolved intra-bundle links are directed
edges. ``render_html`` embeds that projection into a single offline HTML file
(no external scripts, fonts, or fetches — bundle text is untrusted and the
output must work air-gapped). The HTML bytes are deterministic for a given
bundle tree and ``today``.

Layout is computed in the page at view time from a fixed seed, so rendering
is reproducible without storing coordinates in the artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core import indexing
from okf_core.frontmatter import Frontmatter, FrontmatterError, parse_document
from okf_core.revision import read_current_revision_id


def _trust_tier(frontmatter: Frontmatter | None) -> str:
    """OKF v0.2 §5.3: unverified / machine-confirmed / human-reviewed."""
    if frontmatter is None or not frontmatter.verified:
        return "unverified"
    for event in frontmatter.verified:
        if str(event.get("by") or "").startswith("human:"):
            return "human-reviewed"
    return "machine-confirmed"


def build_graph(bundle_root: Path, *, today: str | None = None) -> dict[str, Any]:
    """Project a bundle tree into a JSON-safe graph dict.

    Nodes are concept and reference markdown files (``index.md`` and ``log.md``
    are navigation/log surfaces, not knowledge). Edges are resolved intra-bundle
    links between two nodes; links from ``index.md`` are dropped with it, so the
    graph shows asserted relationships, not the navigation hub.
    """
    files = bundle_mod.markdown_files(bundle_mod.iter_files(bundle_root))
    nodes: list[dict[str, Any]] = []
    node_paths: set[str] = set()
    title = bundle_root.resolve().name

    for file in files:
        rel = file.rel_path
        if bundle_mod.is_index(rel) or bundle_mod.is_log(rel):
            if rel == bundle_mod.INDEX_NAME:
                try:
                    doc = parse_document(file.abs_path.read_text(encoding="utf-8"))
                    if doc.frontmatter is not None:
                        title = doc.frontmatter.title or title
                except FrontmatterError:
                    pass
            continue
        text = file.abs_path.read_text(encoding="utf-8")
        try:
            doc = parse_document(text)
            frontmatter: Frontmatter | None = doc.frontmatter
            body = doc.body
        except FrontmatterError:
            frontmatter, body = None, text
        stale_after = frontmatter.stale_after if frontmatter else None
        nodes.append(
            {
                "path": rel,
                "kind": "reference" if bundle_mod.is_reference(rel) else "concept",
                "title": (frontmatter.title if frontmatter else None)
                or rel.rsplit("/", 1)[-1].removesuffix(".md"),
                "type": frontmatter.type if frontmatter else None,
                "description": frontmatter.description if frontmatter else None,
                "status": frontmatter.status if frontmatter else None,
                "tags": frontmatter.tags if frontmatter else [],
                "trust_tier": _trust_tier(frontmatter),
                "stale": bool(today and stale_after and stale_after < today),
                "chars": len(body),
            }
        )
        node_paths.add(rel)

    outbound = indexing.build_link_graph(bundle_root)["outbound"]
    edges = [
        {"source": source, "target": target}
        for source, targets in outbound.items()
        if source in node_paths
        for target in targets
        if target in node_paths and target != source
    ]

    return {
        "title": title,
        "revision_id": read_current_revision_id(bundle_root),
        "nodes": nodes,
        "edges": edges,
    }


def render_html(graph: dict[str, Any]) -> str:
    """One self-contained HTML page for a ``build_graph`` projection.

    The payload rides in a JSON script block ("</" escaped so bundle text
    cannot break out of it) and every DOM write uses textContent — bundle
    content is untrusted and must never become markup or script.
    """
    payload = json.dumps(graph, sort_keys=True, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__GRAPH_JSON__", payload)


def write_viz(bundle_root: Path, out_path: Path, *, today: str | None = None) -> dict[str, int]:
    """Write the visualization; returns node/edge/byte counts."""
    graph = build_graph(bundle_root, today=today)
    html = render_html(graph)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return {
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "bytes": len(html.encode("utf-8")),
    }


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OKF bundle graph</title>
<style>
  :root { --bg: #0f172a; --panel: #1e293b; --text: #e2e8f0; --muted: #94a3b8;
          --concept: #60a5fa; --reference: #34d399; --edge: #475569; --stale: #f59e0b; }
  * { margin: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--text);
         font: 14px/1.5 system-ui, -apple-system, sans-serif; overflow: hidden; }
  /* Explicit size: with a viewBox the svg has an intrinsic aspect ratio, and
     inset alone would let its height overflow the window. */
  #graph { position: fixed; inset: 0; width: 100%; height: 100%; cursor: grab; }
  #graph:active { cursor: grabbing; }
  header { position: fixed; top: 0; left: 0; padding: 12px 16px; pointer-events: none; }
  header h1 { font-size: 16px; }
  header p { color: var(--muted); font-size: 12px; }
  #legend { position: fixed; bottom: 12px; left: 16px; color: var(--muted);
            font-size: 12px; pointer-events: none; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
         margin: 0 4px 0 10px; }
  #panel { position: fixed; top: 0; right: 0; width: 320px; max-height: 100vh;
           overflow-y: auto; background: var(--panel); padding: 16px;
           display: none; border-left: 1px solid var(--edge); }
  #panel h2 { font-size: 15px; margin-bottom: 2px; }
  #panel .path { color: var(--muted); font-size: 11px; font-family: ui-monospace, monospace;
                 word-break: break-all; margin-bottom: 10px; }
  #panel dl { font-size: 12px; }
  #panel dt { color: var(--muted); margin-top: 8px; }
  #panel p.desc { font-size: 13px; margin-top: 10px; }
  circle.node { stroke: var(--bg); stroke-width: 1.5; cursor: pointer; }
  circle.stale { stroke: var(--stale); stroke-width: 2.5; }
  line.edge { stroke: var(--edge); stroke-width: 1.2; }
  text.label { fill: var(--muted); font-size: 10px; pointer-events: none; }
</style>
</head>
<body>
<svg id="graph"></svg>
<header><h1 id="name"></h1><p id="meta"></p></header>
<div id="legend"><span class="dot" style="background:#60a5fa"></span>concept
<span class="dot" style="background:#34d399"></span>reference
<span class="dot" style="border:2px solid #f59e0b;background:none"></span>stale
&nbsp;·&nbsp; scroll to zoom, drag to pan, click a node</div>
<aside id="panel"></aside>
<script id="data" type="application/json">__GRAPH_JSON__</script>
<script>
"use strict";
const GRAPH = JSON.parse(document.getElementById("data").textContent);
const svg = document.getElementById("graph");
const NS = "http://www.w3.org/2000/svg";
document.title = GRAPH.title + " — OKF bundle graph";
document.getElementById("name").textContent = GRAPH.title;
document.getElementById("meta").textContent =
  GRAPH.nodes.length + " nodes · " + GRAPH.edges.length + " edges" +
  (GRAPH.revision_id ? " · " + GRAPH.revision_id : "");

// Deterministic layout: seeded PRNG (mulberry32), then a fixed number of
// force iterations — same bundle, same picture.
function mulberry32(a) { return function() {
  a |= 0; a = a + 0x6D2B79F5 | 0;
  let t = Math.imul(a ^ a >>> 15, 1 | a);
  t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
  return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const rand = mulberry32(42);
const W = 1200, H = 800;
const nodes = GRAPH.nodes.map(n => ({
  ...n,
  r: 8 + Math.min(14, Math.sqrt(n.chars || 0) / 6),
  x: W / 2 + (rand() - 0.5) * W * 0.8,
  y: H / 2 + (rand() - 0.5) * H * 0.8,
}));
const byPath = new Map(nodes.map(n => [n.path, n]));
const edges = GRAPH.edges
  .map(e => ({ s: byPath.get(e.source), t: byPath.get(e.target) }))
  .filter(e => e.s && e.t);

for (let it = 0; it < 300; it++) {
  for (const a of nodes) {
    let fx = (W / 2 - a.x) * 0.002, fy = (H / 2 - a.y) * 0.002;
    for (const b of nodes) {
      if (a === b) continue;
      const dx = a.x - b.x, dy = a.y - b.y;
      const d2 = Math.max(80, dx * dx + dy * dy);
      fx += dx / d2 * 900; fy += dy / d2 * 900;
    }
    a.x += fx; a.y += fy;
  }
  for (const { s, t } of edges) {
    const dx = t.x - s.x, dy = t.y - s.y;
    const d = Math.max(1, Math.hypot(dx, dy));
    const f = (d - 120) * 0.01;
    s.x += dx / d * f; s.y += dy / d * f;
    t.x -= dx / d * f; t.y -= dy / d * f;
  }
}

// Fit the viewBox to wherever the simulation settled (plus label margin).
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
for (const n of nodes) {
  minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
  minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r + 14);
}
if (!nodes.length) { minX = 0; minY = 0; maxX = W; maxY = H; }
const PAD = 80;
let view = { x: minX - PAD, y: minY - PAD,
             w: maxX - minX + 2 * PAD, h: maxY - minY + 2 * PAD };
function applyView() { svg.setAttribute("viewBox",
  view.x + " " + view.y + " " + view.w + " " + view.h); }
applyView();

function el(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  return node;
}
for (const { s, t } of edges)
  svg.appendChild(el("line", { class: "edge", x1: s.x, y1: s.y, x2: t.x, y2: t.y }));
const panel = document.getElementById("panel");
for (const n of nodes) {
  const c = el("circle", {
    class: "node" + (n.stale ? " stale" : ""), cx: n.x, cy: n.y, r: n.r,
    fill: n.kind === "reference" ? "#34d399" : "#60a5fa",
    "fill-opacity": n.status === "draft" ? 0.55 : 0.9,
  });
  c.addEventListener("click", ev => { ev.stopPropagation(); showPanel(n); });
  svg.appendChild(c);
  const label = el("text", { class: "label", x: n.x, y: n.y + n.r + 12,
                             "text-anchor": "middle" });
  label.textContent = n.title;
  svg.appendChild(label);
}

function showPanel(n) {
  panel.replaceChildren();
  const h = document.createElement("h2"); h.textContent = n.title;
  const path = document.createElement("div");
  path.className = "path"; path.textContent = n.path;
  panel.append(h, path);
  const dl = document.createElement("dl");
  for (const [k, v] of [["kind", n.kind], ["type", n.type], ["status", n.status],
      ["trust", n.trust_tier], ["stale", n.stale ? "yes" : "no"],
      ["tags", (n.tags || []).join(", ") || null]]) {
    if (v == null || v === "") continue;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    dl.append(dt, dd);
  }
  panel.append(dl);
  if (n.description) {
    const p = document.createElement("p");
    p.className = "desc"; p.textContent = n.description;
    panel.append(p);
  }
  panel.style.display = "block";
}
svg.addEventListener("click", () => { panel.style.display = "none"; });

svg.addEventListener("wheel", ev => {
  ev.preventDefault();
  const scale = ev.deltaY > 0 ? 1.1 : 1 / 1.1;
  const mx = view.x + ev.offsetX / svg.clientWidth * view.w;
  const my = view.y + ev.offsetY / svg.clientHeight * view.h;
  view.w *= scale; view.h *= scale;
  view.x = mx - (mx - view.x) * scale;
  view.y = my - (my - view.y) * scale;
  applyView();
}, { passive: false });
let pan = null;
svg.addEventListener("pointerdown", ev => {
  pan = { px: ev.clientX, py: ev.clientY, vx: view.x, vy: view.y };
  svg.setPointerCapture(ev.pointerId);
});
svg.addEventListener("pointermove", ev => {
  if (!pan) return;
  view.x = pan.vx - (ev.clientX - pan.px) / svg.clientWidth * view.w;
  view.y = pan.vy - (ev.clientY - pan.py) / svg.clientHeight * view.h;
  applyView();
});
svg.addEventListener("pointerup", () => { pan = null; });
</script>
</body>
</html>
"""

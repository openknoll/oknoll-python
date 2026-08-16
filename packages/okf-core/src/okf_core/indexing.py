"""Derived bundle indexes: link graph + SQLite FTS5 lexical index.

Indexes are derived state, never bundle content: they live under
``.oknoll/index/<revision-id>/`` keyed by the content-derived revision id, so a
rebuilt index for identical content lands in the same place and stale indexes
are simply unused directories. The SQLite file itself need not be
byte-deterministic — only query *results* must be, which the ranking contract
below guarantees (bm25 score, then path, over deterministic insertion).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core import links as links_mod
from okf_core.frontmatter import FrontmatterError, parse_document
from okf_core.revision import compute_revision_id

INDEX_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/index"
FTS_NAME = "fts.sqlite"
GRAPH_NAME = "graph.json"

# Question words carry no lexical signal; everything else is quoted into FTS terms.
_STOPWORDS = frozenset(
    [
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "here",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "me",
        "must",
        "not",
        "of",
        "on",
        "or",
        "our",
        "over",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "under",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)
# Unicode-aware: an ASCII-only class would silently drop accented and non-Latin
# words, turning a legitimate question into an empty query.
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_QUERY_TERMS = 12


def clip_words(text: str, cap: int) -> str:
    """Length-cap prose at a word boundary, marking the cut with an ellipsis.

    A mid-word cut ("…such as M") reads like corruption wherever the clipped
    text is shown — index lines, overview entries, search hits. The result is
    always ≤ ``cap`` chars and the helper is idempotent, so re-validating
    cached values never re-clips.
    """
    if len(text) <= cap:
        return text
    clipped = text[: cap - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return clipped + "…" if clipped else text[: cap - 1] + "…"


# Process-local memo of indexes built outside a read-only bundle, so repeated
# exploration of the same foreign bundle does not rebuild every time.
_FALLBACK_INDEXES: dict[tuple[str, str], Path] = {}


# Bound on the description a search hit carries: enough for one full sentence,
# small enough that a 25-hit page stays cheap.
MAX_HIT_DESCRIPTION_CHARS = 300


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    kind: str  # concept | reference | index
    title: str
    description: str
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "snippet": self.snippet,
            "score": self.score,
        }


def _kind(rel_path: str) -> str:
    if bundle_mod.is_reference(rel_path):
        return "reference"
    if bundle_mod.is_index(rel_path):
        return "index"
    return "concept"


def build_link_graph(root: Path) -> dict[str, dict[str, list[str]]]:
    """Explicit inbound/outbound link graph over a bundle tree.

    Root-escaping targets are reported by lint, never followed — here they are
    simply absent from the graph.
    """
    outbound: dict[str, list[str]] = {}
    inbound: dict[str, list[str]] = {}
    for file in bundle_mod.markdown_files(bundle_mod.iter_files(root)):
        targets: set[str] = set()
        body = file.abs_path.read_text(encoding="utf-8")
        for link in links_mod.extract_links(body):
            if links_mod.is_external(link.target):
                continue
            resolved = links_mod.resolve_target(link.target, file.rel_path)
            if resolved is not None:
                targets.add(resolved)
        if targets:
            outbound[file.rel_path] = sorted(targets)
            for target in targets:
                inbound.setdefault(target, []).append(file.rel_path)
    return {
        "outbound": dict(sorted(outbound.items())),
        "inbound": {k: sorted(v) for k, v in sorted(inbound.items())},
    }


def write_index(tree: Path, dest_dir: Path) -> Path:
    """Build graph.json + fts.sqlite for a bundle tree, publishing ``dest_dir`` atomically.

    The build happens in a private sibling directory and is moved into place in
    one step, so concurrent readers — a `serve --mcp` session alongside a CLI
    `ask`, or a parallel eval run — never observe a directory that exists but
    holds an empty or half-populated FTS table. A partially built index would
    not just crash a reader: it would answer the same query differently.
    """
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}.", dir=dest_dir.parent))
    try:
        _write_index_files(tree, staging)
        try:
            staging.replace(dest_dir)
        except OSError:
            # Someone published first. The path is content-keyed, so their index
            # is interchangeable with ours — keep theirs rather than deleting a
            # good index to install an identical one, which would open a window
            # where readers find nothing there at all.
            shutil.rmtree(staging, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return dest_dir


def _write_index_files(tree: Path, dest_dir: Path) -> None:
    graph = build_link_graph(tree)
    (dest_dir / GRAPH_NAME).write_text(
        json.dumps(graph, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    con = sqlite3.connect(dest_dir / FTS_NAME)
    try:
        con.execute(
            "CREATE VIRTUAL TABLE docs USING fts5("
            "path UNINDEXED, kind UNINDEXED, title, description, tags, body, "
            "tokenize='porter unicode61')"
        )
        for file in bundle_mod.markdown_files(bundle_mod.iter_files(tree)):
            if bundle_mod.is_log(file.rel_path):
                continue  # log.md belongs to the history tool, not search
            text = file.abs_path.read_text(encoding="utf-8")
            try:
                doc = parse_document(text)
                frontmatter, body = doc.frontmatter, doc.body
                title = (frontmatter.title if frontmatter else None) or file.rel_path
                description = (frontmatter.description if frontmatter else None) or ""
                tags = " ".join(frontmatter.tags) if frontmatter else ""
            except FrontmatterError:
                title, description, tags, body = file.rel_path, "", "", text
            con.execute(
                "INSERT INTO docs (path, kind, title, description, tags, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file.rel_path, _kind(file.rel_path), title, description, tags, body),
            )
        con.commit()
    finally:
        con.close()


def index_dir_for(bundle_root: Path, revision_id: str) -> Path:
    return bundle_root / INDEX_DIR / revision_id


def ensure_index(bundle_root: Path, *, tree: Path | None = None) -> Path:
    """Return the index directory for a bundle tree, building it when missing.

    ``tree`` is the tree to index (default: the bundle root itself). Derived
    state always lands under ``bundle_root`` — so a session pinned to an
    immutable revision directory can index that tree without writing inside
    it, and because the key is the content-derived revision id, a pinned
    revision finds the very index the pipeline built for it. Foreign bundles
    (no ``.oknoll/``) get an index built on first use. A read-only bundle —
    someone else's, or an extracted archive — is indexed into a temp directory
    instead of failing.
    """
    tree = bundle_root if tree is None else tree
    revision_id = compute_revision_id(tree)
    dest = index_dir_for(bundle_root, revision_id)
    if (dest / FTS_NAME).is_file() and (dest / GRAPH_NAME).is_file():
        return dest
    try:
        return write_index(tree, dest)
    except OSError:
        # Read-only bundle. The fallback is a fresh private directory, never a
        # path derived from the revision id: that id is content-derived, so
        # anyone holding the same bundle could predict it and pre-seed a
        # poisoned index on a shared /tmp — poisoned snippets go straight into
        # search output, and a poisoned graph steers the link fan-out.
        cached = _FALLBACK_INDEXES.get((str(tree.resolve()), revision_id))
        if cached is not None and (cached / FTS_NAME).is_file():
            return cached
        fallback = Path(tempfile.mkdtemp(prefix="oknoll-index-")) / revision_id
        write_index(tree, fallback)
        _FALLBACK_INDEXES[(str(tree.resolve()), revision_id)] = fallback
        return fallback


def load_graph(index_dir: Path) -> dict[str, dict[str, list[str]]]:
    raw = json.loads((index_dir / GRAPH_NAME).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {"outbound": {}, "inbound": {}}
    return {
        "outbound": dict(raw.get("outbound", {})),
        "inbound": dict(raw.get("inbound", {})),
    }


def question_terms(text: str) -> list[str]:
    """Salient lowercase terms of a question, in order, stopwords dropped."""
    seen: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _STOPWORDS or token in seen:
            continue
        seen.append(token)
        if len(seen) >= _MAX_QUERY_TERMS:
            break
    return seen


def tokenize(text: str) -> set[str]:
    """All lowercase word tokens of a text (for deterministic overlap scoring)."""
    return set(_TOKEN_RE.findall(text.lower()))


def term_occurrences(text: str, terms: set[str]) -> int:
    """Total occurrences of any of ``terms`` in ``text`` (deterministic tie-break)."""
    return sum(1 for token in _TOKEN_RE.findall(text.lower()) if token in terms)


def fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query over quoted terms."""
    return " OR ".join(f'"{term}"' for term in question_terms(text))


def search_index(index_dir: Path, query: str, *, limit: int = 10) -> list[SearchHit]:
    """Ranked lexical search: bm25 (title-weighted) then path for stable ties."""
    match = fts_query(query)
    if not match:
        return []
    con = sqlite3.connect(index_dir / FTS_NAME)
    try:
        rows = con.execute(
            "SELECT path, kind, title, description, "
            "snippet(docs, 5, '[', ']', ' … ', 12) AS snip, "
            "bm25(docs, 0.0, 0.0, 5.0, 3.0, 2.0, 1.0) AS score "
            "FROM docs WHERE docs MATCH ? ORDER BY score, path LIMIT ?",
            (match, limit),
        ).fetchall()
    finally:
        con.close()
    return [
        SearchHit(
            path=str(path),
            kind=str(kind),
            title=str(title),
            description=clip_words(str(description), MAX_HIT_DESCRIPTION_CHARS),
            snippet=str(snip),
            score=round(float(score), 6),
        )
        for path, kind, title, description, snip, score in rows
    ]

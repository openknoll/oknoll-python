"""The seven deterministic explorer tools.

One implementation shared verbatim by the CLI, the web app, and MCP — every
tool returns a JSON-safe dict and enforces its own guardrail:

- ``overview``  root index, types/tags, trust and freshness summary
- ``list``      directory metadata with type/tag/status filters; bounded count
- ``search``    ranked lexical snippets and paths; no full bodies
- ``peek``      frontmatter and first N lines
- ``read``      one authorized body and its links; size cap
- ``links``     inbound/outbound graph edges; bounded breadth
- ``history``   revision log entries; read-only

Bundle text is untrusted data: nothing here follows external
URLs, executes anything, or resolves a path outside the bundle root.
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core import indexing
from okf_core import links as links_mod
from okf_core.frontmatter import Frontmatter, FrontmatterError, parse_document
from okf_core.revision import read_current_revision_id, revision_dir

TOOLS: tuple[str, ...] = ("overview", "list", "search", "peek", "read", "links", "history")

MAX_LIST_LIMIT = 200
MAX_SEARCH_LIMIT = 25
MAX_PEEK_LINES = 100
MAX_PEEK_CHARS = 8_000
MAX_READ_CHARS = 40_000
MAX_LINK_EDGES = 32
# overview's bounded table of contents: enough to characterize a bundle in one
# call, small enough that "call this first" stays cheap; `list` remains the
# complete, filterable listing.
MAX_OVERVIEW_ENTRIES = 25
MAX_OVERVIEW_DESCRIPTION_CHARS = 240
# peek/read return the whole frontmatter dict; a permissive-consumer bundle can
# carry arbitrarily large frontmatter values, so the projection is bounded too —
# otherwise the body cap above is a paper wall.
MAX_FRONTMATTER_CHARS = 8_000

_LOG_ENTRY_RE = re.compile(r"^- (?P<rev>rev-[0-9a-f]{12}) — (?P<summary>.*)$")


class ExplorerError(ValueError):
    """A tool was asked to violate its guardrail (bad path, bad filter, …)."""


def estimate_tokens(chars: int) -> int:
    """Deterministic budget accounting: ~4 chars per input token."""
    return max(1, chars // 4) if chars else 0


class Explorer:
    """Deterministic navigation over one bundle tree.

    When the bundle has a revision store, the session pins to the current
    immutable revision at construction and reads exclusively from it — the
    answers a session gives are attributable to one revision id, whatever
    rebuilds happen meanwhile. Bundles without a store (foreign or unpacked)
    are navigated via the materialized tree.
    """

    def __init__(self, bundle_root: Path, *, today: str | None = None) -> None:
        root = bundle_root.resolve()
        if not root.is_dir():
            raise ExplorerError(f"bundle directory not found: {bundle_root}")
        self.root = root
        self.today = today  # injectable so freshness stays deterministic in tests
        # A session binds to the current immutable revision when the bundle has
        # a revision store, so a mid-session rebuild cannot change bytes under
        # it. Foreign bundles — someone else's, an extracted archive, no
        # ``.oknoll/`` store — fall back to the materialized tree.
        self.pinned_revision_id: str | None = None
        self._read_root: Path = root
        current = read_current_revision_id(root)
        if current is not None:
            pinned_tree = revision_dir(root, current)
            if pinned_tree.is_dir():
                self.pinned_revision_id = current
                self._read_root = pinned_tree
        self._index_dir: Path | None = None
        self._graph: dict[str, dict[str, list[str]]] | None = None
        self._file_list: list[bundle_mod.BundleFile] | None = None
        self._files: dict[str, Path] | None = None

    # -- shared plumbing ---------------------------------------------------

    def _index(self) -> Path:
        if self._index_dir is None:
            # Index the session's read root, but keep derived state under the
            # bundle root — never inside an immutable revision directory. The
            # index is keyed by content hash, so a pinned session finds the
            # index the pipeline already built for its revision.
            self._index_dir = indexing.ensure_index(self.root, tree=self._read_root)
        return self._index_dir

    def _link_graph(self) -> dict[str, dict[str, list[str]]]:
        if self._graph is None:
            self._graph = indexing.load_graph(self._index())
        return self._graph

    def _files_snapshot(self) -> list[bundle_mod.BundleFile]:
        """The session's file set, enumerated once from the read root.

        Every tool that needs to know "what files are in this bundle?" reads
        this one snapshot, so the seven tools stay mutually consistent even if
        the on-disk tree changes mid-session. In pinned mode the read root is
        an immutable revision directory, so the file set *and* body bytes are
        fixed for the whole session — a mid-session rebuild changes nothing a
        tool returns. In fallback mode (no revision store) the file set is
        still bound at first use, though body content reads live from the
        tree. ``iter_files`` already excludes ``.oknoll/`` and other hidden
        state, and does not descend into symlinked directories, so membership
        here is the authoritative answer to "is this a file of this bundle?".
        """
        if self._file_list is None:
            self._file_list = bundle_mod.iter_files(self._read_root)
        return self._file_list

    def _bundle_files(self) -> dict[str, Path]:
        """The cached rel→abs map over the session's file snapshot."""
        if self._files is None:
            self._files = {f.rel_path: f.abs_path for f in self._files_snapshot()}
        return self._files

    def _safe_rel(self, path: str) -> str:
        """Resolve a caller path to a bundle-root-relative markdown file.

        Rejects traversal, absolute filesystem paths, derived/hidden state, and
        anything outside the bundle root. Bundle-root-absolute ("/concepts/x.md")
        and relative forms are both accepted. The path must name a real bundle
        file *exactly* — on a case-insensitive filesystem "CONCEPTS/OK.md" would
        otherwise open the file but miss every index and graph lookup keyed to
        its true path.
        """
        candidate = path.strip()
        if not candidate:
            raise ExplorerError("empty path")
        normalized = posixpath.normpath(candidate.lstrip("/"))
        parts = normalized.split("/")
        if normalized.startswith("..") or posixpath.isabs(normalized):
            raise ExplorerError(f"path {path!r} escapes the bundle root")
        if any(part.startswith(".") for part in parts):
            raise ExplorerError(f"path {path!r} targets hidden or derived state")
        abs_path = self._bundle_files().get(normalized)
        if abs_path is None:
            raise ExplorerError(f"no such bundle file: {normalized}")
        if not abs_path.resolve().is_relative_to(self._read_root):
            raise ExplorerError(f"path {path!r} escapes the bundle root")
        if not normalized.endswith(".md"):
            raise ExplorerError(f"not a markdown file: {normalized}")
        return normalized

    def _parse(self, rel_path: str) -> tuple[Frontmatter | None, str]:
        # Open the path the file map vetted, never a fresh join of self.root —
        # re-joining would follow a symlink that classification already rejected.
        abs_path = self._bundle_files().get(rel_path)
        if abs_path is None:
            raise ExplorerError(f"no such bundle file: {rel_path}")
        text = abs_path.read_text(encoding="utf-8")
        try:
            doc = parse_document(text)
        except FrontmatterError:
            return None, text
        return doc.frontmatter, doc.body

    @staticmethod
    def _frontmatter_dict(frontmatter: Frontmatter | None) -> dict[str, Any]:
        """A JSON-safe view of frontmatter: YAML permits non-string keys, JSON does not."""
        if frontmatter is None:
            return {}
        return {str(key): value for key, value in frontmatter.data.items()}

    @staticmethod
    def _bounded_frontmatter(frontmatter: Frontmatter | None) -> dict[str, Any]:
        """A JSON-safe frontmatter view bounded to ``MAX_FRONTMATTER_CHARS``.

        peek/read return the whole dict, and a permissive-consumer bundle may
        carry a single frontmatter value megabytes long. Oversized values are
        clipped to a truncation marker and, once the budget is spent, remaining
        keys are dropped with a ``_truncated`` flag — so a foreign bundle cannot
        turn "cheap triage" into an unbounded payload over the MCP transport.
        """
        data = Explorer._frontmatter_dict(frontmatter)
        bounded: dict[str, Any] = {}
        budget = MAX_FRONTMATTER_CHARS
        for key, value in data.items():
            serialized = json.dumps(value, default=str, ensure_ascii=False)
            if len(serialized) > budget:
                if budget > 0:
                    bounded[key] = serialized[:budget] + "…[truncated]"
                    budget = 0
                bounded["_truncated"] = True
                break
            bounded[key] = value
            budget -= len(serialized)
        return bounded

    def _freshness(self, frontmatter: Frontmatter | None) -> dict[str, Any]:
        stale_after = frontmatter.stale_after if frontmatter else None
        stale = bool(self.today and stale_after and stale_after < self.today)
        return {"stale_after": stale_after, "stale": stale}

    # -- the seven tools ---------------------------------------------------

    def _session_revision_id(self) -> str | None:
        """The revision this session answers from: the pin, or the live pointer."""
        if self.pinned_revision_id is not None:
            return self.pinned_revision_id
        return read_current_revision_id(self.root)

    def _index_order(self) -> dict[str, int]:
        """First-appearance rank of each root-index link target — the curated
        table-of-contents order, when an index exists."""
        if bundle_mod.INDEX_NAME not in self._bundle_files():
            return {}
        _, body = self._parse(bundle_mod.INDEX_NAME)
        order: dict[str, int] = {}
        for link in links_mod.extract_links(body):
            if links_mod.is_external(link.target):
                continue
            resolved = links_mod.resolve_target(link.target, bundle_mod.INDEX_NAME)
            if resolved is not None and resolved not in order:
                order[resolved] = len(order)
        return order

    def overview(self) -> dict[str, Any]:
        """Root index, type/tag/status/trust/freshness summary, and a bounded
        table of contents (index order first; ``list`` has the full listing)."""
        index_path = self._read_root / bundle_mod.INDEX_NAME
        title = self.root.name
        description = None
        if index_path.is_file():
            frontmatter, _ = self._parse(bundle_mod.INDEX_NAME)
            if frontmatter is not None:
                title = frontmatter.title or title
                description = frontmatter.description

        types: dict[str, int] = {}
        tags: dict[str, int] = {}
        statuses: dict[str, int] = {}
        verified_count = 0
        stale_count = 0
        concepts = 0
        references = 0
        toc: list[dict[str, Any]] = []
        for file in bundle_mod.markdown_files(self._files_snapshot()):
            is_reference = bundle_mod.is_reference(file.rel_path)
            if not is_reference and not bundle_mod.is_concept(file.rel_path):
                continue
            frontmatter, _ = self._parse(file.rel_path)
            entry_description = frontmatter.description if frontmatter else None
            if entry_description is not None:
                entry_description = entry_description[:MAX_OVERVIEW_DESCRIPTION_CHARS]
            toc.append(
                {
                    "path": file.rel_path,
                    "title": (frontmatter.title if frontmatter else None) or file.rel_path,
                    "type": frontmatter.type if frontmatter else None,
                    "description": entry_description,
                }
            )
            if is_reference:
                references += 1
                continue
            concepts += 1
            if frontmatter is None:
                continue
            if frontmatter.type:
                types[frontmatter.type] = types.get(frontmatter.type, 0) + 1
            for tag in frontmatter.tags:
                tags[tag] = tags.get(tag, 0) + 1
            if frontmatter.status:
                statuses[frontmatter.status] = statuses.get(frontmatter.status, 0) + 1
            if frontmatter.verified:
                verified_count += 1
            if self._freshness(frontmatter)["stale"]:
                stale_count += 1

        # Curated order first — the index's own link order — then anything the
        # index does not link, in sorted-path order. Deterministic either way.
        index_order = self._index_order()
        toc.sort(key=lambda e: (index_order.get(e["path"], len(index_order)), e["path"]))

        return {
            "title": title,
            "description": description,
            "revision_id": self._session_revision_id(),
            "revision_pinned": self.pinned_revision_id is not None,
            "counts": {"concepts": concepts, "references": references},
            "contents": toc[:MAX_OVERVIEW_ENTRIES],
            "contents_total": len(toc),
            "types": dict(sorted(types.items())),
            "tags": dict(sorted(tags.items())),
            "trust": {
                "statuses": dict(sorted(statuses.items())),
                "verified": verified_count,
                "unverified": concepts - verified_count,
            },
            "freshness": {"stale": stale_count},
        }

    def list(
        self,
        directory: str = "concepts",
        *,
        type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Concept metadata under a directory — metadata only, bounded count."""
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        normalized = posixpath.normpath(directory.strip().lstrip("/"))
        if normalized == ".":  # "." and "/" mean the bundle root, not hidden state
            prefix = ""
        elif normalized.startswith("..") or any(
            part.startswith(".") for part in normalized.split("/")
        ):
            raise ExplorerError(f"directory {directory!r} escapes the bundle root")
        else:
            prefix = normalized + "/"

        entries: list[dict[str, Any]] = []
        total = 0
        for file in bundle_mod.markdown_files(self._files_snapshot()):
            if prefix and not file.rel_path.startswith(prefix):
                continue
            if not bundle_mod.is_concept(file.rel_path) and not bundle_mod.is_reference(
                file.rel_path
            ):
                continue
            frontmatter, _ = self._parse(file.rel_path)
            entry_type = frontmatter.type if frontmatter else None
            entry_tags = frontmatter.tags if frontmatter else []
            entry_status = frontmatter.status if frontmatter else None
            if type is not None and entry_type != type:
                continue
            if tag is not None and tag not in entry_tags:
                continue
            if status is not None and entry_status != status:
                continue
            total += 1
            if len(entries) < limit:
                entries.append(
                    {
                        "path": file.rel_path,
                        "title": (frontmatter.title if frontmatter else None) or file.rel_path,
                        "type": entry_type,
                        "status": entry_status,
                        "tags": entry_tags,
                        "description": frontmatter.description if frontmatter else None,
                        **self._freshness(frontmatter),
                    }
                )
        return {"directory": prefix.rstrip("/") or ".", "total": total, "entries": entries}

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        """Ranked lexical snippets and paths; never full bodies (scores recorded)."""
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        hits = indexing.search_index(self._index(), query, limit=limit)
        return {"query": query, "results": [hit.to_dict() for hit in hits]}

    def peek(self, path: str, *, lines: int = 20) -> dict[str, Any]:
        """Frontmatter and the first N body lines — cheap triage before read.

        Char-capped as well as line-capped: a line cap alone lets a foreign
        bundle with one enormous line make "cheap triage" cost more than a
        size-capped read.
        """
        lines = max(1, min(lines, MAX_PEEK_LINES))
        rel = self._safe_rel(path)
        frontmatter, body = self._parse(rel)
        body_lines = body.split("\n")
        body_start = "\n".join(body_lines[:lines])[:MAX_PEEK_CHARS]
        return {
            "path": rel,
            "frontmatter": self._bounded_frontmatter(frontmatter),
            "body_start": body_start,
            "body_lines_total": len(body_lines),
            "truncated": len(body_lines) > lines or len(body_start) < len(body),
        }

    def read(
        self, path: str, *, max_chars: int = MAX_READ_CHARS, start_char: int = 0
    ) -> dict[str, Any]:
        """One authorized body plus its links, size-capped and pageable.

        A document larger than the per-call cap is read in pages: while
        ``truncated`` is true, call again with ``start_char`` set to the
        returned ``next_start`` until it is null. Concatenating the pages
        reproduces the body exactly; every page is a pure function of
        ``(revision, path, start_char, max_chars)``.
        """
        max_chars = max(1, min(max_chars, MAX_READ_CHARS))
        rel = self._safe_rel(path)
        frontmatter, body = self._parse(rel)
        start_char = max(0, min(start_char, len(body)))
        clipped = body[start_char : start_char + max_chars]
        truncated = start_char + len(clipped) < len(body)
        out_links: list[dict[str, Any]] = []
        # Links come from the whole body, not the clipped text: the cap bounds
        # how much prose a caller receives, and a link straddling the cut would
        # otherwise vanish here while `links` still reports it. Every page of a
        # document therefore reports the same link set.
        for link in links_mod.extract_links(body):
            external = links_mod.is_external(link.target)
            resolved = None if external else links_mod.resolve_target(link.target, rel)
            out_links.append(
                {
                    "text": link.text,
                    "target": link.target,
                    "external": external,
                    "resolved": resolved,  # None = external or escapes the root
                }
            )
        return {
            "path": rel,
            "frontmatter": self._bounded_frontmatter(frontmatter),
            "body": clipped,
            "chars": len(clipped),
            "start_char": start_char,
            "body_total_chars": len(body),
            "next_start": start_char + len(clipped) if truncated else None,
            "truncated": truncated,
            "links": out_links,
            **self._freshness(frontmatter),
        }

    def links(self, path: str, *, direction: str = "both", limit: int = 16) -> dict[str, Any]:
        """Inbound/outbound graph edges, bounded breadth; single-bundle only."""
        if direction not in ("both", "inbound", "outbound"):
            raise ExplorerError(f"direction must be both|inbound|outbound, got {direction!r}")
        limit = max(1, min(limit, MAX_LINK_EDGES))
        rel = self._safe_rel(path)
        graph = self._link_graph()
        outbound = graph["outbound"].get(rel, []) if direction != "inbound" else []
        inbound = graph["inbound"].get(rel, []) if direction != "outbound" else []
        return {
            "path": rel,
            "outbound": outbound[:limit],
            "inbound": inbound[:limit],
            "outbound_total": len(graph["outbound"].get(rel, [])),
            "inbound_total": len(graph["inbound"].get(rel, [])),
        }

    def history(self, *, limit: int = 20) -> dict[str, Any]:
        """Revision log entries, newest first; read-only."""
        limit = max(1, min(limit, 100))
        entries: list[dict[str, str]] = []
        log_path = self._read_root / bundle_mod.LOG_NAME
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                match = _LOG_ENTRY_RE.match(line.strip())
                if match:
                    entries.append(
                        {"revision_id": match.group("rev"), "summary": match.group("summary")}
                    )
        entries.reverse()  # log appends chronologically; newest first for callers
        return {
            "current_revision_id": self._session_revision_id(),
            "entries": entries[:limit],
        }

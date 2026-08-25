"""Per-bundle explorer toolkit: the guarded seven-tool surface.

Every knowledge-serving adapter — the CLI's single-bundle stdio MCP server,
the daemon's multi-bundle MCP server, and the daemon HTTP API — is a thin
shell over this toolkit. The guardrails themselves (path validation, bounded
counts, size caps, snippet-only search) are enforced by
``okf_core.explorer.Explorer``; this layer adds the *error discipline* every
transport must share: a refusal message may only ever name the caller's own
bundle-relative paths, and internal failures (a vanished file, unreadable
bytes, a corrupt FTS index) are reported to the operator, never echoed to the
client — OSError embeds absolute host paths and internal errors embed
internal state.

A toolkit serves one bundle for its lifetime and pins the revision the
explorer binds at construction, so the seven tools stay mutually consistent
across a session.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from okf_core.explorer import Explorer, ExplorerError
from okf_core.indexing import FTS_NAME, GRAPH_NAME, write_index
from okf_core.revision import compute_revision_id

TOOL_NAMES = ("overview", "list", "search", "peek", "read", "links", "history")


class ToolkitError(RuntimeError):
    """A refusal whose message is safe to surface to any client."""


class _CacheIndexExplorer(Explorer):
    """An Explorer whose derived index lands in a persistent cache directory.

    Store trees are immutable and write-protected, so the base Explorer falls
    back to a fresh per-process temp index for them. The daemon serves the
    same revisions across many sessions and restarts; its indexes belong in
    the cache plane, keyed by content identity (the revision id of the tree
    actually indexed) so any alias pointing at the same content shares one
    index. ``write_index`` publishes atomically, so concurrent builders are
    safe — the first published index wins and is byte-interchangeable.
    """

    def __init__(self, bundle_root: Path, *, today: str | None, index_cache: Path) -> None:
        super().__init__(bundle_root, today=today)
        self._index_cache = index_cache

    def _index(self) -> Path:
        if self._index_dir is None:
            revision_id = compute_revision_id(self._read_root)
            dest = self._index_cache / revision_id
            if not ((dest / FTS_NAME).is_file() and (dest / GRAPH_NAME).is_file()):
                write_index(self._read_root, dest)
            self._index_dir = dest
        return self._index_dir


class BundleToolkit:
    """The seven deterministic navigation tools over one bundle, guarded."""

    def __init__(
        self,
        bundle_root: Path,
        *,
        today: str | None = None,
        index_cache: Path | None = None,
        on_internal_error: Callable[[str], None] | None = None,
    ) -> None:
        try:
            if index_cache is None:
                self._explorer = Explorer(bundle_root, today=today)
            else:
                self._explorer = _CacheIndexExplorer(
                    bundle_root, today=today, index_cache=index_cache
                )
        except ExplorerError as exc:
            raise ToolkitError(str(exc)) from exc
        self._log = on_internal_error or (lambda msg: print(msg, file=sys.stderr))

    @property
    def pinned_revision_id(self) -> str | None:
        return self._explorer.pinned_revision_id

    def _guarded(self, tool: str, call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        # ExplorerError is a guardrail refusal (bad path, bad filter, …) whose
        # message only ever names the caller's own bundle-relative path — safe
        # to surface. Anything else is an internal failure: log the detail for
        # the operator and hand the client a path-free refusal.
        try:
            return call()
        except ExplorerError as exc:
            raise ToolkitError(f"{tool}: {exc}") from exc
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._log(f"oknoll runtime: {tool} failed: {exc!r}")
            raise ToolkitError(f"{tool}: cannot serve this request") from exc

    # -- the seven tools ---------------------------------------------------

    def overview(self) -> dict[str, Any]:
        return self._guarded("overview", self._explorer.overview)

    def list(
        self,
        directory: str = "concepts",
        *,
        type: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._guarded(
            "list",
            lambda: self._explorer.list(directory, type=type, tag=tag, status=status, limit=limit),
        )

    def search(self, query: str, *, limit: int = 10) -> dict[str, Any]:
        return self._guarded("search", lambda: self._explorer.search(query, limit=limit))

    def peek(self, path: str, *, lines: int = 20) -> dict[str, Any]:
        return self._guarded("peek", lambda: self._explorer.peek(path, lines=lines))

    def read(
        self, path: str, *, max_chars: int | None = None, start_char: int = 0
    ) -> dict[str, Any]:
        if max_chars is None:
            return self._guarded("read", lambda: self._explorer.read(path, start_char=start_char))
        return self._guarded(
            "read",
            lambda: self._explorer.read(path, max_chars=max_chars, start_char=start_char),
        )

    def links(self, path: str, *, direction: str = "both", limit: int = 16) -> dict[str, Any]:
        return self._guarded(
            "links", lambda: self._explorer.links(path, direction=direction, limit=limit)
        )

    def history(self, *, limit: int = 20) -> dict[str, Any]:
        return self._guarded("history", lambda: self._explorer.history(limit=limit))

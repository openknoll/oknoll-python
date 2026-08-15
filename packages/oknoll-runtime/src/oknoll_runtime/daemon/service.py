"""The daemon's core: resolve aliases to pinned toolkits and answer questions.

One `DaemonService` owns the catalog/store handles, a per-identity toolkit
cache, and the ask sessions. Everything it hands out is revision-pinned: a
toolkit is keyed by (alias, manifest digest) so repointing an alias yields a
fresh toolkit while live sessions keep the content they started with —
sessions pin digests, aliases are only mutable pointers.

Derived indexes land in the cache plane keyed by content identity (the
daemon's persistent index cache); traces from daemon asks land in the data
plane next to the CLI's locator-ask traces.

Error discipline: every `ServiceError` message is client-safe — it names
aliases, bundle-relative paths, and public identifiers only, never host
paths. Internal failures are logged for the operator and surfaced as generic
refusals.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from okf_core import EmbeddingProvider, ExplorerError, ModelProvider, answer_question

from oknoll_runtime.catalog import Catalog, CatalogEntry
from oknoll_runtime.dirs import RuntimeDirs
from oknoll_runtime.installer import InstallError, installed_tree
from oknoll_runtime.store import Store, StoreError
from oknoll_runtime.toolkit import TOOL_NAMES, BundleToolkit, ToolkitError

logger = logging.getLogger("oknoll.daemon")

MODES = ("pd", "rag")
MAX_QUESTION_CHARS = 8_000
MAX_SESSIONS = 512

ProviderResolver = Callable[[str | None], ModelProvider]
EmbedderResolver = Callable[[str | None], EmbeddingProvider]


class ServiceError(RuntimeError):
    """A refusal with an HTTP-shaped code; the message is client-safe."""

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(slots=True)
class AskSession:
    """A revision-pinned ask session: identity fixed at creation."""

    id: str
    alias: str
    oci_digest: str
    okf_revision: str
    mode: str
    created_at: str
    turns: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.id,
            "bundle": self.alias,
            "oci_digest": self.oci_digest,
            "okf_revision": self.okf_revision,
            "mode": self.mode,
            "created_at": self.created_at,
            "turns": self.turns,
        }


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class DaemonService:
    """Catalog-backed, revision-pinned access to every installed bundle."""

    def __init__(
        self,
        dirs: RuntimeDirs,
        *,
        provider_resolver: ProviderResolver | None = None,
        embedder_resolver: EmbedderResolver | None = None,
        today: str | None = None,
    ) -> None:
        self.dirs = dirs
        self.store = Store(dirs.data)
        self.catalog = Catalog(dirs.data)
        self.index_cache = dirs.cache / "index"
        self._provider_resolver = provider_resolver
        self._embedder_resolver = embedder_resolver
        self._today = today
        self._lock = threading.Lock()
        self._toolkits: dict[tuple[str, str], BundleToolkit] = {}
        self._sessions: dict[str, AskSession] = {}

    # -- catalog -----------------------------------------------------------

    def entries(self) -> list[CatalogEntry]:
        return self.catalog.list_entries()

    def get_entry(self, alias: str) -> CatalogEntry:
        entry = self.catalog.get(alias)
        if entry is None:
            raise ServiceError("unknown_bundle", f"no installed bundle named {alias!r}", status=404)
        return entry

    @staticmethod
    def qualified(entry: CatalogEntry) -> str:
        """The immutable identity citations carry: ``alias@sha256:…``."""
        return f"{entry.alias}@{entry.oci_digest}"

    def entry_dict(self, entry: CatalogEntry) -> dict[str, Any]:
        lint: Any
        try:
            lint = json.loads(entry.lint_summary) if entry.lint_summary else {}
        except json.JSONDecodeError:
            lint = {}
        return {
            "alias": entry.alias,
            "bundle": self.qualified(entry),
            "mode": entry.mode,
            "oci_digest": entry.oci_digest,
            "okf_revision": entry.okf_revision,
            "title": entry.title,
            "description": entry.description,
            "publisher": entry.publisher,
            "signature_state": entry.signature_state,
            "lint_summary": lint,
            "installed_at": entry.installed_at,
            "last_used_at": entry.last_used_at,
        }

    # -- toolkits ----------------------------------------------------------

    def _tree_for(self, entry: CatalogEntry) -> Path:
        try:
            return installed_tree(self.store, entry)
        except (InstallError, StoreError) as exc:
            # InstallError messages name the alias only; store content that
            # vanished under a catalog entry is an operator problem.
            logger.warning("content tree unavailable for %r: %r", entry.alias, exc)
            raise ServiceError(
                "content_missing",
                f"content for {entry.alias!r} is missing from the local store",
                status=410,
            ) from exc

    def toolkit(self, alias: str) -> tuple[BundleToolkit, CatalogEntry]:
        """The pinned toolkit for an alias (cached per content identity)."""
        entry = self.get_entry(alias)
        key = (entry.alias, entry.oci_digest)
        with self._lock:
            cached = self._toolkits.get(key)
            if cached is not None:
                return cached, entry
        tree = self._tree_for(entry)
        try:
            built = BundleToolkit(
                tree,
                today=self._today or date.today().isoformat(),
                index_cache=self.index_cache,
                on_internal_error=logger.warning,
            )
        except ToolkitError as exc:
            logger.warning("toolkit for %r failed to open: %r", alias, exc)
            raise ServiceError(
                "content_missing",
                f"content for {alias!r} cannot be served from the local store",
                status=410,
            ) from exc
        with self._lock:
            self._toolkits.setdefault(key, built)
            result = self._toolkits[key]
        return result, entry

    def call_tool(self, alias: str, tool: str, /, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Run one of the seven tools; result carries the qualified identity."""
        if tool not in TOOL_NAMES:
            raise ServiceError("unknown_tool", f"unknown tool {tool!r}", status=404)
        toolkit, entry = self.toolkit(alias)
        method = getattr(toolkit, tool)
        try:
            result: dict[str, Any] = method(*args, **kwargs)
        except ToolkitError as exc:
            raise ServiceError("refused", str(exc), status=400) from exc
        except TypeError as exc:
            raise ServiceError("bad_request", f"{tool}: invalid arguments", status=400) from exc
        self.catalog.touch_last_used(entry.alias)
        return {"bundle": self.qualified(entry), "okf_revision": entry.okf_revision, **result}

    # -- ask sessions ------------------------------------------------------

    def create_session(self, alias: str, *, mode: str = "pd") -> AskSession:
        if mode not in MODES:
            raise ServiceError("bad_request", f"unknown retrieval mode {mode!r} (pd|rag)")
        entry = self.get_entry(alias)
        self._tree_for(entry)  # fail now, not on the first turn
        session = AskSession(
            id=secrets.token_hex(8),
            alias=entry.alias,
            oci_digest=entry.oci_digest,
            okf_revision=entry.okf_revision,
            mode=mode,
            created_at=_now(),
        )
        with self._lock:
            while len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions.values(), key=lambda s: (s.created_at, s.id))
                del self._sessions[oldest.id]
            self._sessions[session.id] = session
        return session

    def get_session(self, session_id: str) -> AskSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ServiceError("unknown_session", "no such session", status=404)
        return session

    def _session_tree(self, session: AskSession) -> Path:
        """The tree a session pinned at creation — alias moves change nothing."""
        entry = CatalogEntry(
            alias=session.alias,
            mode="local",
            reference="",
            oci_digest=session.oci_digest,
            okf_revision=session.okf_revision,
        )
        return self._tree_for(entry)

    # -- ask ---------------------------------------------------------------

    def ask(
        self,
        *,
        question: str,
        alias: str | None = None,
        session_id: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        embedder: str | None = None,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ServiceError("bad_request", "question must not be empty")
        if len(question) > MAX_QUESTION_CHARS:
            raise ServiceError("bad_request", f"question exceeds {MAX_QUESTION_CHARS} characters")

        session: AskSession | None = None
        if session_id is not None:
            session = self.get_session(session_id)
            if alias is not None and alias != session.alias:
                raise ServiceError(
                    "bad_request", "session and bundle disagree — pass one or the other"
                )
            if mode is not None and mode != session.mode:
                raise ServiceError(
                    "bad_request",
                    f"session is pinned to mode {session.mode!r}; repeated arguments must match",
                )
            ask_mode = session.mode
            tree = self._session_tree(session)
            identity = f"{session.alias}@{session.oci_digest}"
            revision = session.okf_revision
            trace_alias = session.alias
        elif alias is not None:
            ask_mode = mode or "pd"
            if ask_mode not in MODES:
                raise ServiceError("bad_request", f"unknown retrieval mode {ask_mode!r} (pd|rag)")
            entry = self.get_entry(alias)
            tree = self._tree_for(entry)
            identity = self.qualified(entry)
            revision = entry.okf_revision
            trace_alias = entry.alias
        else:
            raise ServiceError("bad_request", "pass a bundle alias or a session id")

        if self._provider_resolver is None:
            raise ServiceError(
                "not_configured", "this daemon has no model provider configured", status=501
            )
        try:
            provider = self._provider_resolver(model)
            embedder_impl = (
                self._embedder_resolver(embedder)
                if ask_mode == "rag" and self._embedder_resolver is not None
                else None
            )
        except Exception as exc:  # provider specs are operator config, not client data
            logger.warning("provider resolution failed: %r", exc)
            raise ServiceError(
                "not_configured", "the requested model provider is not available", status=501
            ) from exc

        try:
            result = answer_question(
                bundle_dir=tree,
                question=question,
                provider=provider,
                today=self._today or date.today().isoformat(),
                condition=ask_mode,
                embedder=embedder_impl,
            )
        except (ExplorerError, ValueError) as exc:
            raise ServiceError("refused", str(exc), status=400) from exc
        except Exception as exc:  # provider/network failure — never echo detail
            logger.warning("ask against %r failed: %r", trace_alias, exc)
            raise ServiceError(
                "provider_error", "the model provider failed to answer", status=502
            ) from exc

        if session is not None:
            with session.lock:
                session.turns += 1
        self.catalog.touch_last_used(trace_alias)
        trace_name = self._write_trace(trace_alias, result.to_dict())

        budget = result.trace.get("budget", {})
        payload: dict[str, Any] = {
            "bundle": identity,
            "okf_revision": revision,
            "mode": ask_mode,
            "answer": result.answer,
            "citations": [
                {
                    "path": c.path,
                    "source_ids": list(c.source_ids),
                    "qualified": f"{identity}/{c.path.lstrip('/')}",
                }
                for c in result.citations
            ],
            "warnings": list(result.warnings),
            "trace": {
                "id": trace_name,
                "tools": len(result.trace.get("tools", [])),
                "spent_chars": budget.get("spent_chars"),
                "spent_tokens": budget.get("spent_tokens"),
            },
        }
        if session is not None:
            payload["session"] = session.id
            payload["turns"] = session.turns
        return payload

    def _write_trace(self, alias: str, result_dict: dict[str, Any]) -> str:
        """Write the full trace to the data plane; return its name, not its path."""
        traces = self.dirs.data / "traces" / alias
        traces.mkdir(parents=True, exist_ok=True)
        stamp = (
            str(result_dict.get("trace", {}).get("started_at", ""))
            .replace(":", "")
            .replace("-", "")
        )
        base = f"ask-{stamp}"
        path = traces / f"{base}.json"
        counter = 2
        while path.exists():
            path = traces / f"{base}-{counter}.json"
            counter += 1
        path.write_text(
            json.dumps(result_dict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return path.name

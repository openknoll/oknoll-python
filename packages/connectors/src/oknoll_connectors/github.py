"""GitHub connector: repository docs with commit-SHA provenance.

MVP behavior: repository tree, README/docs and selected
sources (include patterns), ignore rules, commit-SHA provenance. Issues, PRs,
and releases are deferred.

Acquisition uses the GitHub REST API through ``SafeFetcher`` (the full
FetchPolicy network envelope applies). The resolved commit SHA is part of every
item's URI (``https://github.com/o/r/blob/<sha>/<path>``), so provenance in the
bundle's reference snapshots always names the exact commit, and checkpoints pin
the SHA so a resumed acquisition continues over the identical snapshot.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import json
import re
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, ClassVar

from okf_core import Anchor, Block, CanonicalDoc, Link, SourceRef, sha256_hex
from okf_core import links as md_links

from oknoll_connectors.fetch import FetchResponse, SafeFetcher
from oknoll_connectors.protocol import (
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    ProbeResult,
    RawItem,
)
from oknoll_connectors.textnorm import markdown_blocks, paragraphs_with_lines

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"

MEDIA_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".rst": "text/plain",
}

#: Default selection: README anywhere, everything under docs/, Markdown and
#: plain text anywhere. `include` patterns replace this set ("selected sources").
DEFAULT_INCLUDE: tuple[str, ...] = ("README*", "docs/*", "*.md", "*.markdown", "*.txt")

_HTTPS_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)"
    r"(?:\.git)?(?:/tree/(?P<ref>[^\s]+?))?/?$"
)
_SCP_RE = re.compile(r"^git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?$")
_SSH_RE = re.compile(r"^ssh://git@github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")


def _utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_fetcher_factory(policy: FetchPolicy) -> SafeFetcher:
    return SafeFetcher(policy)


def parse_github_source(uri: str) -> tuple[str, str, str | None] | None:
    """(owner, repo, ref-or-None) for any recognized GitHub repository form."""
    text = uri.strip()
    for pattern in (_HTTPS_RE, _SCP_RE, _SSH_RE):
        match = pattern.match(text)
        if match:
            groups = match.groupdict()
            return groups["owner"], groups["repo"], groups.get("ref") or None
    return None


class GitHubConnector:
    """Connector for public (or token-accessible) GitHub repositories."""

    id = "github"
    version = "0.1.0"
    capabilities: ClassVar[set[str]] = {"repository", "markdown", "text", "checkpoint"}

    def __init__(
        self,
        *,
        fetcher_factory: Callable[[FetchPolicy], SafeFetcher] | None = None,
        api_base: str = API_BASE,
        token: str | None = None,
        include: tuple[str, ...] = DEFAULT_INCLUDE,
        ignore_patterns: tuple[str, ...] = (),
        clock: Callable[[], str] | None = None,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> None:
        self._fetcher_factory = fetcher_factory or default_fetcher_factory
        self._api_base = api_base.rstrip("/")
        self._token = token
        self._include = include
        self._ignore_patterns = ignore_patterns
        self._clock = clock or _utc_now
        self._resume_after: str | None = None
        self._pinned_sha: str | None = None
        self._last_uri: str | None = None
        if checkpoint is not None:
            if checkpoint.connector != self.id:
                raise ConnectorError(
                    f"checkpoint belongs to connector {checkpoint.connector!r}, not {self.id!r}"
                )
            cursor_uri = checkpoint.cursor.get("last_uri")
            cursor_sha = checkpoint.cursor.get("commit_sha")
            if isinstance(cursor_uri, str):
                self._resume_after = cursor_uri
                self._last_uri = cursor_uri
            if isinstance(cursor_sha, str):
                self._pinned_sha = cursor_sha

    # -- protocol ---------------------------------------------------------

    def probe(self, source: SourceRef) -> ProbeResult:
        """Offline probe: URL shape only — acquisition applies the full policy."""
        parsed = parse_github_source(source.uri)
        if parsed is None:
            return ProbeResult(
                False, self.id, "unknown", f"not a GitHub repository URL: {source.uri}"
            )
        owner, repo, ref = parsed
        detail = f"{owner}/{repo}" + (f"@{ref}" if ref else "")
        return ProbeResult(True, self.id, "repository", detail)

    def acquire(self, source: SourceRef, policy: FetchPolicy) -> Iterator[RawItem]:
        parsed = parse_github_source(source.uri)
        if parsed is None:
            raise ConnectorError(f"github: not a GitHub repository URL: {source.uri}")
        owner, repo, ref = parsed
        fetcher = self._fetcher_factory(policy)

        sha = self._pinned_sha or self._resolve_commit(fetcher, owner, repo, ref)
        self._pinned_sha = sha
        entries = self._tree(fetcher, owner, repo, sha)

        selected = sorted(
            (entry for entry in entries if self._selected(str(entry["path"]))),
            key=lambda entry: str(entry["path"]),
        )
        for entry in selected:
            path = str(entry["path"])
            uri = f"https://github.com/{owner}/{repo}/blob/{sha}/{path}"
            if self._resume_after is not None and uri <= self._resume_after:
                continue
            size = int(entry.get("size", 0))
            if size > policy.max_item_bytes:
                raise ConnectorError(
                    f"github: {uri} is {size} bytes (policy max_item_bytes={policy.max_item_bytes})"
                )
            media_type = _media_type(path)
            if media_type is None:  # pragma: no cover - _selected already filters
                continue
            data = self._blob(fetcher, owner, repo, str(entry["sha"]), path)
            item = RawItem(
                uri=uri,
                media_type=media_type,
                data=data,
                source_hash=sha256_hex(data),
                retrieved_at=self._clock(),
                connector=self.id,
                connector_version=self.version,
                metadata={
                    "repository": f"{owner}/{repo}",
                    "commit_sha": sha,
                    "path": path,
                    "blob_sha": str(entry["sha"]),
                    "bytes": len(data),
                },
            )
            self._last_uri = uri  # set before yield: checkpoint() after next() must see it
            yield item

    def normalize(self, item: RawItem) -> Iterable[CanonicalDoc]:
        try:
            text = item.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConnectorError(f"github: {item.uri} is not valid UTF-8: {exc}") from exc

        blocks: list[Block]
        anchors: list[Anchor]
        links: list[Link]
        if item.media_type == "text/markdown":
            blocks, anchors = markdown_blocks(text)
            title = next(
                (b.text for b in blocks if b.kind == "heading" and b.level == 1),
                self._title_fallback(item),
            )
            links = [
                Link(target=link.target, text=link.text) for link in md_links.extract_links(text)
            ]
        elif item.media_type == "text/plain":
            blocks = []
            anchors = []
            for start_line, paragraph in paragraphs_with_lines(text):
                anchor_id = f"L{start_line}"
                anchors.append(Anchor(id=anchor_id, kind="line", value=str(start_line)))
                blocks.append(Block(kind="paragraph", text=paragraph, anchor_id=anchor_id))
            title = self._title_fallback(item)
            links = []
        else:
            raise ConnectorError(f"github: cannot normalize media type {item.media_type!r}")

        doc = CanonicalDoc(
            id=f"doc-{sha256_hex(item.uri)[:12]}",
            source_ref=SourceRef(connector=self.id, uri=item.uri),
            source_hash=item.source_hash,
            retrieved_at=item.retrieved_at,
            title=title,
            blocks=tuple(blocks),
            anchors=tuple(anchors),
            links=tuple(links),
            metadata={"media_type": item.media_type, **item.metadata},
        )
        return [doc]

    def checkpoint(self) -> ConnectorCheckpoint | None:
        if self._last_uri is None:
            return None
        return ConnectorCheckpoint(
            connector=self.id,
            version=self.version,
            cursor={"last_uri": self._last_uri, "commit_sha": self._pinned_sha},
        )

    # -- selection ---------------------------------------------------------

    def _selected(self, path: str) -> bool:
        if _media_type(path) is None:
            return False
        if self._ignored(tuple(PurePosixPath(path).parts)):
            return False
        basename = PurePosixPath(path).name
        return any(
            fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(basename, pattern)
            for pattern in self._include
        )

    def _ignored(self, rel_parts: tuple[str, ...]) -> bool:
        for pattern in self._ignore_patterns:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in rel_parts[:-1]:
                    return True
            elif any(fnmatch.fnmatch(part, pattern) for part in rel_parts):
                return True
        return False

    def _title_fallback(self, item: RawItem) -> str:
        path = str(item.metadata.get("path", item.uri))
        stem = PurePosixPath(path).stem
        return stem.replace("-", " ").replace("_", " ").strip() or path

    # -- GitHub API --------------------------------------------------------

    def _api(self, fetcher: SafeFetcher, path: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self._api_base}{path}"
        response: FetchResponse = fetcher.fetch(url, headers=headers)
        if response.status != 200:
            raise ConnectorError(
                f"github: GET {url} returned HTTP {response.status}"
                + (" (rate limited or private repository?)" if response.status == 403 else "")
            )
        try:
            return json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError(f"github: GET {url} returned invalid JSON: {exc}") from exc

    def _resolve_commit(self, fetcher: SafeFetcher, owner: str, repo: str, ref: str | None) -> str:
        if ref is None:
            repo_info = self._api(fetcher, f"/repos/{owner}/{repo}")
            ref = str(repo_info.get("default_branch", "main"))
        commit = self._api(fetcher, f"/repos/{owner}/{repo}/commits/{ref}")
        sha = commit.get("sha")
        if not isinstance(sha, str) or not sha:
            raise ConnectorError(f"github: could not resolve {owner}/{repo}@{ref} to a commit")
        return sha

    def _tree(self, fetcher: SafeFetcher, owner: str, repo: str, sha: str) -> list[dict[str, Any]]:
        tree = self._api(fetcher, f"/repos/{owner}/{repo}/git/trees/{sha}?recursive=1")
        if tree.get("truncated"):
            raise ConnectorError(
                f"github: tree for {owner}/{repo}@{sha[:12]} is truncated — "
                "narrow the selection with include patterns"
            )
        entries = tree.get("tree")
        if not isinstance(entries, list):
            raise ConnectorError(f"github: unexpected tree response for {owner}/{repo}")
        return [entry for entry in entries if entry.get("type") == "blob"]

    def _blob(self, fetcher: SafeFetcher, owner: str, repo: str, blob_sha: str, path: str) -> bytes:
        blob = self._api(fetcher, f"/repos/{owner}/{repo}/git/blobs/{blob_sha}")
        encoding = blob.get("encoding")
        content = blob.get("content", "")
        if encoding != "base64" or not isinstance(content, str):
            raise ConnectorError(f"github: unexpected blob encoding {encoding!r} for {path}")
        try:
            return base64.b64decode(content, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ConnectorError(f"github: undecodable blob content for {path}: {exc}") from exc


def _media_type(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in MEDIA_TYPES:
        return MEDIA_TYPES[suffix]
    if not suffix and PurePosixPath(path).name.upper().startswith("README"):
        return "text/plain"
    return None

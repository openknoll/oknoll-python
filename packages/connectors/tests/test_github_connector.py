"""GitHub connector: shared contract suite + selection/provenance tests.

Tests run offline against a canned GitHub REST API served by a fake transport
under the real SafeFetcher, so the production request path (policy checks,
headers, JSON handling) is exercised.
"""

import base64
import json
from pathlib import Path

import pytest
from okf_core import SourceRef, sha256_hex
from oknoll_connectors import ConnectorCheckpoint, ConnectorError, FetchPolicy, GitHubConnector
from oknoll_connectors.fetch import SafeFetcher, TransportRequest, TransportResponse
from oknoll_connectors.github import DEFAULT_INCLUDE, parse_github_source
from oknoll_connectors.testing import ConnectorContractSuite, ConnectorFixture

PUBLIC_IP = "140.82.121.6"
FIXED_CLOCK = "2026-08-03T00:00:00Z"
COMMIT_SHA = "3fcbb9f828c2f23d109c855ee403c3a4c81f3a96"

FILES: dict[str, str] = {
    "README.md": "# Handbook\n\nWelcome to the acme handbook.\n\nSee [the guide](docs/guide.md).\n",
    "CHANGELOG.md": "# Changelog\n\n## 0.1.0\n\n- First release.\n",
    "docs/guide.md": "# Guide\n\nHow we work.\n\n## Reviews\n\nEvery change gets a review.\n",
    "docs/notes.txt": "Meeting notes.\n\nDecisions are recorded here.\n",
    "drafts/wip.md": "# WIP\n\nNot ready.\n",
    "src/main.py": "print('hello')\n",
    "assets/logo.png": "\x89PNG-not-really",
}


def _blob_sha(path: str) -> str:
    return sha256_hex(path)[:40]


class FakeGitHubTransport:
    """Serves a canned GitHub REST API for repository acme/handbook."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.headers_seen: list[dict[str, str]] = []

    def send(self, request: TransportRequest, max_bytes: int) -> TransportResponse:
        assert request.ip == PUBLIC_IP
        assert request.host == "api.github.com"
        self.requests.append(request.path)
        self.headers_seen.append(dict(request.headers))
        body = self._route(request.path)
        if body is None:
            return TransportResponse(
                404, {"content-type": "application/json"}, b'{"message":"Not Found"}'
            )
        return TransportResponse(
            200, {"content-type": "application/json"}, json.dumps(body).encode()
        )

    def _route(self, path: str) -> object | None:
        if path == "/repos/acme/handbook":
            return {"default_branch": "main"}
        if path in ("/repos/acme/handbook/commits/main", "/repos/acme/handbook/commits/dev"):
            return {"sha": COMMIT_SHA}
        if path == f"/repos/acme/handbook/git/trees/{COMMIT_SHA}?recursive=1":
            return {
                "truncated": False,
                "tree": [
                    {
                        "path": file_path,
                        "type": "blob",
                        "sha": _blob_sha(file_path),
                        "size": len(FILES[file_path]),
                    }
                    for file_path in sorted(FILES)
                ]
                + [{"path": "docs", "type": "tree", "sha": _blob_sha("docs")}],
            }
        for file_path, content in FILES.items():
            if path == f"/repos/acme/handbook/git/blobs/{_blob_sha(file_path)}":
                encoded = base64.b64encode(content.encode()).decode()
                # GitHub wraps base64 payloads in newlines; decoding must cope.
                wrapped = "\n".join(encoded[i : i + 60] for i in range(0, len(encoded), 60))
                return {"encoding": "base64", "content": wrapped}
        return None


def make_github(
    checkpoint: ConnectorCheckpoint | None = None,
    *,
    transport: FakeGitHubTransport | None = None,
    include: tuple[str, ...] | None = None,
    ignore_patterns: tuple[str, ...] = (),
    token: str | None = None,
) -> tuple[GitHubConnector, FakeGitHubTransport]:
    fake = transport if transport is not None else FakeGitHubTransport()
    connector = GitHubConnector(
        fetcher_factory=lambda policy: SafeFetcher(
            policy,
            transport=fake,
            resolver=lambda host: (PUBLIC_IP,),
            sleep=lambda seconds: None,
        ),
        include=include if include is not None else DEFAULT_INCLUDE,
        ignore_patterns=ignore_patterns,
        token=token,
        clock=lambda: FIXED_CLOCK,
        checkpoint=checkpoint,
    )
    return connector, fake


REPO = SourceRef(connector="github", uri="https://github.com/acme/handbook")

EXPECTED_PATHS = ["CHANGELOG.md", "README.md", "docs/guide.md", "docs/notes.txt", "drafts/wip.md"]


class TestGitHubConnectorContract(ConnectorContractSuite):
    def make_fixture(self, tmp_path: Path) -> ConnectorFixture:
        return ConnectorFixture(
            make_connector=lambda checkpoint: make_github(checkpoint)[0],
            source=REPO,
            policy=FetchPolicy(),
            min_items=5,
            malformed_source=SourceRef(connector="github", uri="https://github.com/acme/missing"),
        )


def test_parse_github_source_forms() -> None:
    assert parse_github_source("https://github.com/acme/handbook") == ("acme", "handbook", None)
    assert parse_github_source("https://github.com/acme/handbook.git") == (
        "acme",
        "handbook",
        None,
    )
    assert parse_github_source("https://github.com/acme/handbook/tree/dev") == (
        "acme",
        "handbook",
        "dev",
    )
    assert parse_github_source("git@github.com:acme/handbook.git") == ("acme", "handbook", None)
    assert parse_github_source("ssh://git@github.com/acme/handbook") == ("acme", "handbook", None)
    assert parse_github_source("https://example.com/acme/handbook") is None
    assert parse_github_source("https://github.com/acme") is None


def test_items_carry_commit_sha_provenance() -> None:
    connector, _ = make_github()
    items = list(connector.acquire(REPO, FetchPolicy()))
    assert [item.metadata["path"] for item in items] == EXPECTED_PATHS
    for item in items:
        assert item.metadata["commit_sha"] == COMMIT_SHA
        assert item.uri == (
            f"https://github.com/acme/handbook/blob/{COMMIT_SHA}/{item.metadata['path']}"
        )


def test_default_selection_excludes_code_and_binaries() -> None:
    connector, _ = make_github()
    paths = [item.metadata["path"] for item in connector.acquire(REPO, FetchPolicy())]
    assert "src/main.py" not in paths
    assert "assets/logo.png" not in paths


def test_include_patterns_select_sources() -> None:
    connector, _ = make_github(include=("docs/*",))
    paths = [item.metadata["path"] for item in connector.acquire(REPO, FetchPolicy())]
    assert paths == ["docs/guide.md", "docs/notes.txt"]


def test_ignore_patterns_prune_paths() -> None:
    connector, _ = make_github(ignore_patterns=("drafts/",))
    paths = [item.metadata["path"] for item in connector.acquire(REPO, FetchPolicy())]
    assert "drafts/wip.md" not in paths


def test_ref_in_url_is_used_for_commit_resolution() -> None:
    connector, transport = make_github()
    source = SourceRef(connector="github", uri="https://github.com/acme/handbook/tree/dev")
    list(connector.acquire(source, FetchPolicy()))
    assert "/repos/acme/handbook/commits/dev" in transport.requests
    assert "/repos/acme/handbook" not in transport.requests  # no default-branch lookup


def test_checkpoint_pins_commit_sha() -> None:
    connector, _ = make_github()
    iterator = iter(connector.acquire(REPO, FetchPolicy()))
    first = next(iterator)
    checkpoint = connector.checkpoint()
    assert checkpoint is not None
    assert checkpoint.cursor["commit_sha"] == COMMIT_SHA
    assert checkpoint.cursor["last_uri"] == first.uri

    resumed, transport = make_github(checkpoint)
    items = list(resumed.acquire(REPO, FetchPolicy()))
    assert [item.metadata["path"] for item in items] == EXPECTED_PATHS[1:]
    # the pinned SHA skips ref resolution entirely
    assert "/repos/acme/handbook/commits/main" not in transport.requests


def test_token_is_sent_as_bearer_header() -> None:
    connector, transport = make_github(token="ghp_secret")
    list(connector.acquire(REPO, FetchPolicy()))
    assert all(
        headers.get("Authorization") == "Bearer ghp_secret" for headers in transport.headers_seen
    )


def test_markdown_normalization_and_links() -> None:
    connector, _ = make_github()
    items = {item.metadata["path"]: item for item in connector.acquire(REPO, FetchPolicy())}
    readme = next(iter(connector.normalize(items["README.md"])))
    assert readme.title == "Handbook"
    assert any(link.target == "docs/guide.md" for link in readme.links)
    assert readme.metadata["commit_sha"] == COMMIT_SHA

    notes = next(iter(connector.normalize(items["docs/notes.txt"])))
    assert [a.kind for a in notes.anchors] == ["line", "line"]


def test_probe_is_offline() -> None:
    connector, transport = make_github()
    result = connector.probe(REPO)
    assert result.supported and result.kind == "repository"
    assert result.detail == "acme/handbook"
    assert transport.requests == []
    assert not connector.probe(
        SourceRef(connector="github", uri="https://gitlab.com/a/b")
    ).supported


def test_oversize_blob_raises() -> None:
    connector, _ = make_github()
    with pytest.raises(ConnectorError, match="max_item_bytes"):
        list(connector.acquire(REPO, FetchPolicy(max_item_bytes=10)))


def test_checkpoint_from_other_connector_rejected() -> None:
    foreign = ConnectorCheckpoint(connector="web", version="0.1.0", cursor={"last_uri": "x"})
    with pytest.raises(ConnectorError, match="belongs to connector"):
        GitHubConnector(checkpoint=foreign)

"""Connector contract (design §6.2/§6.3).

A connector acquires and normalizes. It never writes OKF — okf-core owns
generation, provenance, validation, indexing, and packing so there is one
dialect. ``FetchPolicy`` is the shared safety envelope; every connector must
pass the shared contract suite in ``oknoll_connectors.testing``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

from okf_core import CanonicalDoc, SourceRef


class ConnectorError(RuntimeError):
    """Raised for unusable sources or malformed input; never allowed to crash a build."""


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Shared safety limits (design §6.3).

    The files connector uses the local caps; the network fields bind the web and
    GitHub connectors arriving in Phase 3 and are part of the frozen contract now.
    """

    # Local acquisition caps.
    max_item_bytes: int = 20 * 1024 * 1024
    max_items: int = 1_000
    follow_symlinks: bool = False

    # Network policy (enforced by Phase 3 connectors).
    allowed_schemes: tuple[str, ...] = ("https", "http")
    timeout_seconds: float = 30.0
    max_redirects: int = 5
    allow_private_addresses: bool = False
    per_host_requests_per_second: float = 1.0
    max_decompressed_bytes: int = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What a connector makes of a source before acquiring it."""

    supported: bool
    connector: str
    kind: str  # "file" | "directory" | "url" | "repository" | "transcript" | "unknown"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RawItem:
    """One acquired item with full provenance (design §5.3 stage 1)."""

    uri: str
    media_type: str
    data: bytes
    source_hash: str  # sha256 of data
    retrieved_at: str  # ISO-8601 UTC
    connector: str
    connector_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorCheckpoint:
    """Opaque resume cursor; a new connector instance created with it continues
    acquisition after the last fully yielded item."""

    connector: str
    version: str
    cursor: dict[str, Any]


@runtime_checkable
class Connector(Protocol):
    """The frozen connector protocol (design §6.2)."""

    id: str
    version: str
    capabilities: ClassVar[set[str]]

    def probe(self, source: SourceRef) -> ProbeResult: ...

    def acquire(self, source: SourceRef, policy: FetchPolicy) -> Iterable[RawItem]: ...

    def normalize(self, item: RawItem) -> Iterable[CanonicalDoc]: ...

    def checkpoint(self) -> ConnectorCheckpoint | None: ...


def acquire_all(connector: Connector, source: SourceRef, policy: FetchPolicy) -> Iterator[RawItem]:
    """Convenience: acquire with the policy item cap enforced uniformly."""
    for count, item in enumerate(connector.acquire(source, policy)):
        if count >= policy.max_items:
            raise ConnectorError(
                f"{connector.id}: source {source.uri!r} exceeds max_items={policy.max_items}"
            )
        yield item

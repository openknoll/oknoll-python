"""CanonicalDoc: the format-neutral internal domain model (design §4.1, ADR-0002).

Connectors emit CanonicalDoc records; generation, linking, linting, indexing, and
packing consume them. OKF v0.2 is one serializer over this model, never the model
itself. Instances are immutable after normalization; dict serialization is
deterministic and round-trip stable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal

BlockKind = Literal["heading", "paragraph", "code", "list", "quote", "table", "raw"]
AnchorKind = Literal["heading", "page", "line", "time"]


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a document came from: a connector id plus a connector-scoped URI."""

    connector: str
    uri: str

    def to_dict(self) -> dict[str, Any]:
        return {"connector": self.connector, "uri": self.uri}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(connector=data["connector"], uri=data["uri"])


@dataclass(frozen=True, slots=True)
class Block:
    """One ordered content block. `level` applies to headings, `language` to code."""

    kind: BlockKind
    text: str
    level: int | None = None
    language: str | None = None
    anchor_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "text": self.text}
        if self.level is not None:
            out["level"] = self.level
        if self.language is not None:
            out["language"] = self.language
        if self.anchor_id is not None:
            out["anchor_id"] = self.anchor_id
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Block:
        return cls(
            kind=data["kind"],
            text=data["text"],
            level=data.get("level"),
            language=data.get("language"),
            anchor_id=data.get("anchor_id"),
        )


@dataclass(frozen=True, slots=True)
class Anchor:
    """A stable addressable location inside the source (heading, page, line, time)."""

    id: str
    kind: AnchorKind
    value: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        return cls(id=data["id"], kind=data["kind"], value=data["value"])


@dataclass(frozen=True, slots=True)
class Link:
    """An outgoing link discovered in the source."""

    target: str
    text: str = ""
    anchor_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"target": self.target, "text": self.text}
        if self.anchor_id is not None:
            out["anchor_id"] = self.anchor_id
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Link:
        return cls(
            target=data["target"], text=data.get("text", ""), anchor_id=data.get("anchor_id")
        )


@dataclass(frozen=True, slots=True)
class MediaRef:
    """A reference to non-text media associated with the document."""

    id: str
    uri: str
    media_type: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "uri": self.uri, "media_type": self.media_type}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MediaRef:
        return cls(id=data["id"], uri=data["uri"], media_type=data["media_type"])


@dataclass(frozen=True, slots=True)
class CanonicalDoc:
    """One normalized document emitted by a connector (design §4.1)."""

    id: str
    source_ref: SourceRef
    source_hash: str
    retrieved_at: str  # ISO-8601 UTC timestamp, kept as text for determinism
    title: str
    blocks: tuple[Block, ...] = ()
    anchors: tuple[Anchor, ...] = ()
    links: tuple[Link, ...] = ()
    media_refs: tuple[MediaRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    rights_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_ref": self.source_ref.to_dict(),
            "source_hash": self.source_hash,
            "retrieved_at": self.retrieved_at,
            "title": self.title,
            "blocks": [b.to_dict() for b in self.blocks],
            "anchors": [a.to_dict() for a in self.anchors],
            "links": [link.to_dict() for link in self.links],
            "media_refs": [m.to_dict() for m in self.media_refs],
            "metadata": self.metadata,
            "rights_hint": self.rights_hint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalDoc:
        return cls(
            id=data["id"],
            source_ref=SourceRef.from_dict(data["source_ref"]),
            source_hash=data["source_hash"],
            retrieved_at=data["retrieved_at"],
            title=data["title"],
            blocks=tuple(Block.from_dict(b) for b in data.get("blocks", [])),
            anchors=tuple(Anchor.from_dict(a) for a in data.get("anchors", [])),
            links=tuple(Link.from_dict(link) for link in data.get("links", [])),
            media_refs=tuple(MediaRef.from_dict(m) for m in data.get("media_refs", [])),
            metadata=dict(data.get("metadata", {})),
            rights_hint=data.get("rights_hint"),
        )

    def content_hash(self) -> str:
        """Deterministic hash over the normalized content (blocks only)."""
        payload = "\x00".join(
            f"{b.kind}\x01{b.level}\x01{b.language}\x01{b.text}" for b in self.blocks
        )
        return sha256_hex(payload)

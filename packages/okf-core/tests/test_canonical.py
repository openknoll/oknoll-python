"""CanonicalDoc serialization round trip and hash determinism (ADR-0002)."""

from okf_core import Anchor, Block, CanonicalDoc, Link, SourceRef


def _doc() -> CanonicalDoc:
    return CanonicalDoc(
        id="doc-001",
        source_ref=SourceRef(connector="files", uri="file:///architecture.docx"),
        source_hash="sha256:abc",
        retrieved_at="2026-07-30T10:00:00Z",
        title="Architecture",
        blocks=(
            Block(kind="heading", text="Summary", level=1, anchor_id="summary"),
            Block(kind="paragraph", text="A modular monolith."),
            Block(kind="code", text="print('hi')", language="python"),
        ),
        anchors=(Anchor(id="summary", kind="heading", value="Summary"),),
        links=(Link(target="auth.md", text="Auth"),),
        metadata={"pages": 12},
        rights_hint="internal",
    )


def test_dict_round_trip() -> None:
    doc = _doc()
    assert CanonicalDoc.from_dict(doc.to_dict()) == doc


def test_content_hash_is_stable() -> None:
    assert _doc().content_hash() == _doc().content_hash()


def test_content_hash_tracks_blocks_only() -> None:
    doc = _doc()
    retitled = CanonicalDoc.from_dict({**doc.to_dict(), "title": "Renamed"})
    assert retitled.content_hash() == doc.content_hash()
    edited = CanonicalDoc.from_dict(
        {**doc.to_dict(), "blocks": [{"kind": "paragraph", "text": "Different."}]}
    )
    assert edited.content_hash() != doc.content_hash()

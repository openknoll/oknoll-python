"""Round-trip and determinism contract for the OKF frontmatter parser/writer (ADR-0003)."""

from pathlib import Path

import pytest
from okf_core import (
    FrontmatterShapeError,
    FrontmatterYamlError,
    parse_document,
    write_document,
)

WIDGET = Path(__file__).resolve().parents[3] / (
    "fixtures/bundles/golden/unknown-fields/concepts/widget.md"
)


def test_parse_known_fields() -> None:
    doc = parse_document(WIDGET.read_text(encoding="utf-8"))
    fm = doc.frontmatter
    assert fm is not None
    assert fm.type == "WobbleSpec"
    assert fm.title == "Widget calibration"
    assert fm.status == "stable"
    assert fm.generated == {"by": "foreign-tool/9.9", "at": "2026-06-01T00:00:00Z"}


def test_timestamps_stay_strings() -> None:
    doc = parse_document(WIDGET.read_text(encoding="utf-8"))
    assert doc.frontmatter is not None
    generated = doc.frontmatter.generated
    assert generated is not None
    assert isinstance(generated["at"], str)


def test_round_trip_preserves_unknown_fields() -> None:
    original = parse_document(WIDGET.read_text(encoding="utf-8"))
    assert original.frontmatter is not None
    rewritten = parse_document(write_document(original))
    fm = rewritten.frontmatter
    assert fm is not None
    assert fm.data["x_custom_priority"] == 7
    assert fm.data["x_review"] == {"board": "widgets", "cycle": "2026-Q3"}
    assert fm.data["vendor_flags"] == ["alpha", "beta"]
    assert fm.data["openknoll_note"] == "namespaced extension key"
    assert fm.unknown_keys() == ("x_custom_priority", "x_review", "vendor_flags")
    assert original.frontmatter.data == fm.data


def test_write_is_idempotent_and_deterministic() -> None:
    text = WIDGET.read_text(encoding="utf-8")
    once = write_document(parse_document(text))
    twice = write_document(parse_document(once))
    assert once == twice


def test_writer_emits_canonical_key_order() -> None:
    text = "---\nx_last: 1\ntitle: T\ntype: Reference\n---\n\nBody.\n"
    written = write_document(parse_document(text))
    assert written.index("type:") < written.index("title:") < written.index("x_last:")


def test_document_without_frontmatter() -> None:
    doc = parse_document("Just a body.\n")
    assert doc.frontmatter is None
    assert write_document(doc) == "Just a body.\n"


def test_unterminated_frontmatter_raises() -> None:
    with pytest.raises(FrontmatterYamlError):
        parse_document("---\ntitle: open\n")


def test_non_mapping_frontmatter_raises() -> None:
    with pytest.raises(FrontmatterShapeError):
        parse_document("---\n- just\n- a list\n---\n\nBody.\n")

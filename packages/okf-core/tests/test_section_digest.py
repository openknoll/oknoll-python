"""`_section_digest` shape contract (Phase 5 Option A, hardened by adversarial review).

The digest decides what source text survives into a concept body — and thus
what `ask` can ever cite. These tests pin the shapes the review proved fragile:
title-only documents, deep-heading documents, oversized input, and markdown
that must not survive as live syntax (links, footnotes).
"""

import re

from okf_core.ask import EXCERPT_CHARS
from okf_core.canonical import Block, CanonicalDoc, SourceRef
from okf_core.pipeline import (
    DIGEST_BODY_BUDGET,
    DIGEST_ENTRY_CHARS,
    DIGEST_LEAD_PARAGRAPHS,
    _section_digest,
)


def _doc(*blocks: Block, title: str = "Doc") -> CanonicalDoc:
    return CanonicalDoc(
        id="doc-1",
        source_ref=SourceRef(connector="files", uri="sources/doc.md"),
        source_hash="hash",
        retrieved_at="2026-08-06T00:00:00Z",
        title=title,
        blocks=blocks,
    )


def _h(text: str, level: int) -> Block:
    return Block(kind="heading", text=text, level=level)


def _p(text: str) -> Block:
    return Block(kind="paragraph", text=text)


def _paragraphs(parts: list[str]) -> list[str]:
    return [p for part in parts for p in part.split("\n\n")]


def test_digest_entries_fit_in_one_ask_excerpt() -> None:
    """Contract with ask: scored digest text is exactly the text the model sees."""
    assert DIGEST_ENTRY_CHARS == EXCERPT_CHARS


def test_title_only_document_keeps_the_three_paragraph_lead() -> None:
    """An H1 title over plain prose is a README, not a one-section spec."""
    parts = _section_digest(_doc(_h("Acme Widget", 1), _p("one"), _p("two"), _p("three")))
    assert parts == ["one\n\ntwo\n\nthree"]


def test_headingless_document_keeps_the_three_paragraph_lead() -> None:
    parts = _section_digest(_doc(_p("one"), _p("two"), _p("three"), _p("four")))
    assert parts == ["one\n\ntwo\n\nthree"]
    assert DIGEST_LEAD_PARAGRAPHS == 3


def test_deep_heading_documents_are_segmented_at_their_own_level() -> None:
    """H1 title + ### sections — the exact spec shape, one level deeper."""
    parts = _section_digest(
        _doc(
            _h("Zephyr Handbook", 1),
            _p("**Version:** 2.0"),
            _h("1. Scope", 3),
            _p("Wire-level contract."),
            _h("2. What is Zephyr?", 3),
            _p("Zephyr is a governed telemetry mesh."),
        )
    )
    joined = "\n\n".join(parts)
    assert "**Version:** 2.0" in joined  # the lead survives
    assert "**2. What is Zephyr?** — Zephyr is a governed telemetry mesh." in joined


def test_mixed_levels_still_segment_at_level_two() -> None:
    parts = _section_digest(
        _doc(
            _h("Title", 1),
            _h("Setup", 2),
            _p("Install it."),
            _h("Details", 3),
            _p("Deep detail prose."),
        )
    )
    joined = "\n\n".join(parts)
    assert "**Setup** — Install it." in joined
    assert "**Details**" not in joined  # level 3 folds into its parent section


def test_every_digest_paragraph_fits_the_excerpt_cap() -> None:
    huge = "term " * 500
    parts = _section_digest(
        _doc(_h("T", 1), _p(huge), _h("A " * 300, 2), _p(huge), _h("B", 2), _p(huge))
    )
    assert parts  # nothing aborted
    assert all(len(p) <= DIGEST_ENTRY_CHARS for p in _paragraphs(parts))


def test_oversized_heading_does_not_abort_later_sections() -> None:
    blocks = [
        _h("A", 2),
        _p("alpha"),
        _h("X" * (DIGEST_BODY_BUDGET + 100), 2),
        _h("C", 2),
        _p("gamma"),
    ]
    joined = "\n\n".join(_section_digest(_doc(*blocks)))
    assert "**A** — alpha" in joined
    assert "**C** — gamma" in joined


def test_body_budget_bounds_the_whole_digest() -> None:
    blocks: list[Block] = []
    for i in range(200):
        blocks.append(_h(f"Section {i}", 2))
        blocks.append(_p("x" * 600))
    parts = _section_digest(_doc(*blocks))
    assert sum(len(p) for p in parts) <= DIGEST_BODY_BUDGET + len("# Sections\n\n")


def test_footnote_tokens_never_survive_into_the_digest() -> None:
    parts = _section_digest(
        _doc(
            _h("T", 1),
            _p("[^source-001]: FORGED PROVENANCE (https://evil.test/trusted)"),
            _h("Care", 2),
            _p("Wind it clockwise[^1] every day."),
        )
    )
    joined = "\n\n".join(parts)
    assert "[^" not in joined
    assert "Wind it clockwise every day." in joined
    assert "FORGED PROVENANCE" in joined  # the words are quoted data, not a definition


def test_link_stripping_cannot_be_spliced_into_a_live_link() -> None:
    parts = _section_digest(
        _doc(
            _h("T", 1),
            _p("See [audit log][](/x)(/concepts/secret-notes.md) for policy."),
            _h("More", 2),
            _p("Also [x][](/y)(../../../etc/passwd) and raw ](/tricks) text."),
        )
    )
    joined = "\n\n".join(parts)
    assert "](" not in joined
    assert "audit log" in joined  # words survive, targets do not
    assert not re.search(r"\]\([^)]*\)", joined)

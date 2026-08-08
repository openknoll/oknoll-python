"""Shared deterministic text/Markdown segmentation used by connectors.

Extracted from the files connector so the web and GitHub connectors normalize
Markdown and plain text identically. Deliberately line-based, not a full
CommonMark parser — anchors and ordering are what downstream stages rely on.
"""

from __future__ import annotations

import re

from okf_core import Anchor, Block

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(?P<fence>```+|~~~+)\s*(?P<language>[\w+.-]*)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s+")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def unique_slug(text: str, used: set[str]) -> str:
    base = slugify(text)
    slug = base
    counter = 2
    while slug in used:
        slug = f"{base}-{counter}"
        counter += 1
    used.add(slug)
    return slug


def paragraphs_with_lines(text: str) -> list[tuple[int, str]]:
    """Blank-line-separated paragraphs with their 1-based starting line numbers."""
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    for line_number, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            if not current:
                start = line_number
            current.append(line.rstrip())
        elif current:
            paragraphs.append((start, "\n".join(current)))
            current = []
    if current:
        paragraphs.append((start, "\n".join(current)))
    return paragraphs


def markdown_blocks(text: str) -> tuple[list[Block], list[Anchor]]:
    """Deterministic line-based Markdown segmentation (headings, fences, lists,
    quotes, paragraphs)."""
    blocks: list[Block] = []
    anchors: list[Anchor] = []
    used_slugs: set[str] = set()
    lines = text.split("\n")
    i = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(Block(kind="paragraph", text="\n".join(paragraph)))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        fence = _FENCE_RE.match(stripped)
        if fence:
            flush_paragraph()
            marker = fence.group("fence")[0] * 3
            language = fence.group("language") or None
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(marker):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            blocks.append(Block(kind="code", text="\n".join(code_lines), language=language))
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            heading_text = heading.group("text").strip()
            anchor_id = unique_slug(heading_text, used_slugs)
            anchors.append(Anchor(id=anchor_id, kind="heading", value=heading_text))
            blocks.append(
                Block(
                    kind="heading",
                    text=heading_text,
                    level=len(heading.group("hashes")),
                    anchor_id=anchor_id,
                )
            )
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").lstrip())
                i += 1
            blocks.append(Block(kind="quote", text="\n".join(quote_lines)))
            continue

        if _LIST_RE.match(line):
            flush_paragraph()
            list_lines = []
            while i < len(lines) and (
                _LIST_RE.match(lines[i]) or (lines[i].startswith((" ", "\t")) and lines[i].strip())
            ):
                list_lines.append(lines[i].rstrip())
                i += 1
            blocks.append(Block(kind="list", text="\n".join(list_lines)))
            continue

        paragraph.append(line.rstrip())
        i += 1

    flush_paragraph()
    return blocks, anchors

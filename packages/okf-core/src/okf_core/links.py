"""Markdown link and footnote extraction plus bundle-root path resolution.

Bundle-root-absolute links start with "/" (design §4.3). Relative links resolve
against the containing file's directory. Resolution never touches the
filesystem; escaping the bundle root is reported, not followed.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

_LINK_RE = re.compile(r"(?<!\!)\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(?P<id>[^\]]+)\]:", re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r"\[\^(?P<id>[^\]]+)\](?!:)")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True, slots=True)
class MarkdownLink:
    text: str
    target: str


def extract_links(body: str) -> list[MarkdownLink]:
    return [
        MarkdownLink(text=m.group("text"), target=m.group("target"))
        for m in _LINK_RE.finditer(body)
    ]


def footnote_definitions(body: str) -> set[str]:
    return {m.group("id") for m in _FOOTNOTE_DEF_RE.finditer(body)}


def footnote_references(body: str) -> set[str]:
    return {m.group("id") for m in _FOOTNOTE_REF_RE.finditer(body)}


def is_external(target: str) -> bool:
    """True for URLs with a scheme (https:, mailto:, …) and pure fragments."""
    return bool(_SCHEME_RE.match(target)) or target.startswith("#")


def resolve_target(target: str, from_path: str) -> str | None:
    """Resolve a link target to a bundle-root-relative posix path.

    `from_path` is the linking file's bundle-root-relative path. Returns None
    when the resolved path escapes the bundle root (path traversal).
    """
    target = target.split("#", 1)[0]
    if not target:
        return None
    if target.startswith("/"):
        joined = target.lstrip("/")
    else:
        joined = posixpath.join(posixpath.dirname(from_path), target)
    normalized = posixpath.normpath(joined)
    if normalized.startswith("..") or posixpath.isabs(normalized):
        return None
    return normalized

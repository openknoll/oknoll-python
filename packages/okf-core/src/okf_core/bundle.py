"""Bundle tree model: reserved files, deterministic walking, file classification.

Layout: index.md, log.md, manifest.json are reserved; concepts
live in Markdown files; `references/` holds acquired source material (not
concepts); `.oknoll/` is local derived state and is never part of the bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RESERVED_FILES: tuple[str, ...] = ("index.md", "log.md", "manifest.json")
DERIVED_STATE_DIR = ".oknoll"
REFERENCES_DIR = "references"

INDEX_NAME = "index.md"
LOG_NAME = "log.md"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class BundleFile:
    rel_path: str  # bundle-root-relative posix path
    abs_path: Path


def iter_files(root: Path) -> list[BundleFile]:
    """All bundle files, deterministically ordered, excluding derived/hidden state.

    Classification follows the *resolved* path, not the name a symlink wears.
    A link named ``concepts/x.md`` pointing at ``../.oknoll/traces/t.json`` or at
    somewhere else on disk is not bundle content, and treating it as content
    would leak that data into search snippets and packed archives — and make the
    revision id and manifest depend on files the bundle does not own, so editing
    a local trace would change the published revision. (``rglob`` already
    refuses to descend into symlinked directories.)
    """
    resolved_root = root.resolve()
    files: list[BundleFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_hidden(rel):
            continue  # .oknoll/, .git/, hidden files
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            continue  # symlink out of the bundle: not ours to read, hash, or pack
        if _is_hidden(resolved.relative_to(resolved_root).as_posix()):
            continue  # symlink into derived state, wearing a content-looking name
        files.append(BundleFile(rel_path=rel, abs_path=path))
    return files


def _is_hidden(rel_path: str) -> bool:
    return any(part.startswith(".") for part in rel_path.split("/"))


def markdown_files(files: list[BundleFile]) -> list[BundleFile]:
    return [f for f in files if f.rel_path.endswith(".md")]


def is_index(rel_path: str) -> bool:
    return rel_path.rsplit("/", 1)[-1] == INDEX_NAME


def is_log(rel_path: str) -> bool:
    return rel_path == LOG_NAME


def is_reference(rel_path: str) -> bool:
    return rel_path.startswith(f"{REFERENCES_DIR}/")


def is_concept(rel_path: str) -> bool:
    """Concept files carry OKF frontmatter and are subject to full lint."""
    return (
        rel_path.endswith(".md")
        and not is_index(rel_path)
        and not is_log(rel_path)
        and not is_reference(rel_path)
    )

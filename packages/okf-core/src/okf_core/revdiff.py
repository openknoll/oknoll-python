"""Semantic diff between two published revisions of one bundle.

File-level truth is a byte comparison over the two immutable revision trees.
The semantic layer classifies changes by bundle role — concepts (retitles) and
references (source re-pins, read from ``openknoll_source`` frontmatter) — and
diffs the link graphs. ``log.md`` and ``manifest.json`` are bookkeeping, not
content (revision ids exclude them), so they never appear in a report.
Everything returned is JSON-safe and deterministically ordered, so the same
two revisions always produce the same report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core.frontmatter import Frontmatter, FrontmatterError, parse_document
from okf_core.indexing import build_link_graph
from okf_core.revision import REVISIONS_DIR, revision_dir

_BOOKKEEPING = (bundle_mod.LOG_NAME, bundle_mod.MANIFEST_NAME)


class RevisionDiffError(ValueError):
    """A revision id could not be resolved to a published revision tree."""


def available_revisions(bundle_dir: Path) -> list[str]:
    """The published revision ids of a bundle, sorted."""
    store = bundle_dir / REVISIONS_DIR
    if not store.is_dir():
        return []
    return sorted(p.name for p in store.iterdir() if p.is_dir() and p.name.startswith("rev-"))


def resolve_revision(bundle_dir: Path, revision_id: str) -> Path:
    tree = revision_dir(bundle_dir, revision_id)
    if not tree.is_dir():
        known = available_revisions(bundle_dir)
        listing = ", ".join(known) if known else "none published"
        raise RevisionDiffError(
            f"no revision {revision_id!r} in this bundle (available: {listing})"
        )
    return tree


def diff_revisions(bundle_dir: Path, rev_a: str, rev_b: str) -> dict[str, Any]:
    """Report what changed between two published revisions (``rev_b`` is "after")."""
    tree_a = resolve_revision(bundle_dir, rev_a)
    tree_b = resolve_revision(bundle_dir, rev_b)

    files_a = _content_files(tree_a)
    files_b = _content_files(tree_b)
    added = sorted(files_b.keys() - files_a.keys())
    removed = sorted(files_a.keys() - files_b.keys())
    changed = sorted(
        rel
        for rel in files_a.keys() & files_b.keys()
        if files_a[rel].read_bytes() != files_b[rel].read_bytes()
    )

    concepts: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    references: dict[str, list[dict[str, Any]]] = {"added": [], "removed": [], "changed": []}
    for rel in added:
        entry = {"path": rel, "title": _title(files_b[rel], rel)}
        if bundle_mod.is_concept(rel):
            concepts["added"].append(entry)
        elif bundle_mod.is_reference(rel):
            references["added"].append(entry)
    for rel in removed:
        entry = {"path": rel, "title": _title(files_a[rel], rel)}
        if bundle_mod.is_concept(rel):
            concepts["removed"].append(entry)
        elif bundle_mod.is_reference(rel):
            references["removed"].append(entry)
    for rel in changed:
        if bundle_mod.is_concept(rel):
            title_a = _title(files_a[rel], rel)
            title_b = _title(files_b[rel], rel)
            concepts["changed"].append(
                {
                    "path": rel,
                    "retitled": title_a != title_b,
                    "title_a": title_a,
                    "title_b": title_b,
                }
            )
        elif bundle_mod.is_reference(rel):
            pin_a = _source_pin(files_a[rel])
            pin_b = _source_pin(files_b[rel])
            uri_changed = pin_a.get("uri") != pin_b.get("uri")
            hash_changed = pin_a.get("source_hash") != pin_b.get("source_hash")
            references["changed"].append(
                {
                    "path": rel,
                    "repinned": uri_changed or hash_changed,
                    "uri_changed": uri_changed,
                    "source_hash_changed": hash_changed,
                }
            )

    edges_a = _edges(tree_a)
    edges_b = _edges(tree_b)

    return {
        "rev_a": rev_a,
        "rev_b": rev_b,
        "files": {"added": added, "removed": removed, "changed": changed},
        "concepts": concepts,
        "references": references,
        "links": {
            "added": [list(edge) for edge in sorted(edges_b - edges_a)],
            "removed": [list(edge) for edge in sorted(edges_a - edges_b)],
        },
    }


def _content_files(tree: Path) -> dict[str, Path]:
    return {
        file.rel_path: file.abs_path
        for file in bundle_mod.iter_files(tree)
        if file.rel_path not in _BOOKKEEPING
    }


def _frontmatter(path: Path) -> Frontmatter | None:
    try:
        return parse_document(path.read_text(encoding="utf-8")).frontmatter
    except (FrontmatterError, UnicodeDecodeError):
        return None


def _title(path: Path, rel: str) -> str:
    frontmatter = _frontmatter(path)
    return (frontmatter.title if frontmatter else None) or rel


def _source_pin(path: Path) -> dict[str, Any]:
    """The reference's ``openknoll_source`` provenance block, or empty."""
    frontmatter = _frontmatter(path)
    raw = frontmatter.data.get("openknoll_source") if frontmatter else None
    return raw if isinstance(raw, dict) else {}


def _edges(tree: Path) -> set[tuple[str, str]]:
    outbound = build_link_graph(tree)["outbound"]
    return {(src, dst) for src, targets in outbound.items() for dst in targets}

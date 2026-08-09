"""Immutable local revisions with an atomic current-revision flip.

Layout under the bundle directory:

- ``.oknoll/revisions/<revision-id>/`` — complete, immutable bundle trees.
- ``.oknoll/current`` — the current revision id, replaced atomically.
- top level — the current revision materialized as a plain Markdown tree, so
  the file stays the contract for anyone browsing the directory.

Revision ids are content-derived (sha256 over the content files, excluding
``log.md`` and ``manifest.json``), which is what makes rebuilds idempotent and
``oknoll diff --check`` meaningful.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from okf_core import bundle as bundle_mod

REVISIONS_DIR = f"{bundle_mod.DERIVED_STATE_DIR}/revisions"
CURRENT_POINTER = f"{bundle_mod.DERIVED_STATE_DIR}/current"
LOG_HEADER = "# Revision log\n"


def compute_revision_id(staged_dir: Path) -> str:
    """Content-derived revision id over everything except log.md and manifest.json."""
    digest = hashlib.sha256()
    for file in bundle_mod.iter_files(staged_dir):
        if file.rel_path in (bundle_mod.LOG_NAME, bundle_mod.MANIFEST_NAME):
            continue
        digest.update(file.rel_path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(file.abs_path.read_bytes())
        digest.update(b"\x00")
    return f"rev-{digest.hexdigest()[:12]}"


def build_manifest(root: Path) -> dict[str, object]:
    """Deterministic manifest matching the lint schema: {files: {path: {sha256, bytes}}}."""
    files: dict[str, dict[str, object]] = {}
    for file in bundle_mod.iter_files(root):
        if file.rel_path == bundle_mod.MANIFEST_NAME:
            continue
        data = file.abs_path.read_bytes()
        files[file.rel_path] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    return {"okf_version": "0.2", "files": dict(sorted(files.items()))}


def write_manifest(root: Path) -> None:
    manifest = build_manifest(root)
    (root / bundle_mod.MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def read_current_revision_id(bundle_dir: Path) -> str | None:
    pointer = bundle_dir / CURRENT_POINTER
    if not pointer.is_file():
        return None
    value = pointer.read_text(encoding="utf-8").strip()
    return value or None


def revision_dir(bundle_dir: Path, revision_id: str) -> Path:
    return bundle_dir / REVISIONS_DIR / revision_id


def parent_log_text(bundle_dir: Path) -> str:
    """The current revision's log, or a fresh header when no revision exists."""
    current = read_current_revision_id(bundle_dir)
    if current is not None:
        log_path = revision_dir(bundle_dir, current) / bundle_mod.LOG_NAME
        if log_path.is_file():
            return log_path.read_text(encoding="utf-8")
    return LOG_HEADER


def log_with_entry(parent_log: str, revision_id: str, summary: str) -> str:
    body = parent_log.rstrip("\n")
    last_line = body.rsplit("\n", 1)[-1]
    separator = "\n" if last_line.startswith("- ") else "\n\n"
    return f"{body}{separator}- {revision_id} — {summary}\n"


def publish_revision(bundle_dir: Path, staged_dir: Path, revision_id: str) -> None:
    """Freeze the staged tree as an immutable revision and flip the pointer."""
    target = revision_dir(bundle_dir, revision_id)
    if not target.exists():  # published revisions are immutable; never rewrite
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(staged_dir, tmp)
        tmp.replace(target)

    pointer = bundle_dir / CURRENT_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer_tmp = pointer.with_name("current.tmp")
    pointer_tmp.write_text(revision_id + "\n", encoding="utf-8")
    pointer_tmp.replace(pointer)

    materialize(bundle_dir, revision_id)


def materialize(bundle_dir: Path, revision_id: str) -> None:
    """Mirror a revision's files at the bundle top level (derived state untouched)."""
    source = revision_dir(bundle_dir, revision_id)
    for entry in bundle_dir.iterdir():
        if entry.name.startswith("."):
            continue  # .oknoll/ and other local dotfiles are not revision content
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    shutil.copytree(source, bundle_dir, dirs_exist_ok=True)


def compare_trees(expected_dir: Path, actual_dir: Path) -> list[tuple[str, str]]:
    """Byte-compare two bundle trees; returns (path, drift-kind) pairs, sorted."""
    expected = {f.rel_path: f.abs_path for f in bundle_mod.iter_files(expected_dir)}
    actual = {f.rel_path: f.abs_path for f in bundle_mod.iter_files(actual_dir)}
    drift: list[tuple[str, str]] = []
    for path in sorted(expected.keys() | actual.keys()):
        if path not in actual:
            drift.append((path, "missing-from-rebuild"))
        elif path not in expected:
            drift.append((path, "unexpected-in-rebuild"))
        elif expected[path].read_bytes() != actual[path].read_bytes():
            drift.append((path, "content-differs"))
    return drift

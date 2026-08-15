"""Install and checkout: the consumer pipeline over store + catalog.

``install_bundle`` composes the brief's sequence — resolve input → safe
extract → verify archive and manifest integrity → lint (warnings preserved,
never blocking: foreign bundles stay installable) → preserve content bytes →
store blobs by digest → register a pinned catalog alias. Integrity failures
(checksum mismatches against the bundle's own manifest) do block: they mean
tampering or corruption, not foreign dialect.

``checkout_bundle`` materializes the Git-like human-visible tree with local
``.oknoll/`` state pinned to the installed revision.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from okf_core import ExtractLimits, LintConfig, lint_bundle
from okf_core import bundle as bundle_mod
from okf_core.revision import publish_revision

from oknoll_runtime.catalog import Catalog, CatalogEntry
from oknoll_runtime.image import ImageRecord, build_image_from_archive, build_image_from_tree
from oknoll_runtime.store import Store, write_unprotect_tree


class InstallError(RuntimeError):
    pass


def verify_bundle_manifest(tree: Path) -> list[str]:
    """Byte-verify a tree against its own manifest.json; [] means intact.

    A missing manifest is not a failure (permissive consumer — foreign
    bundles may not carry one); the caller records that as a warning.
    """
    manifest_path = tree / bundle_mod.MANIFEST_NAME
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared_raw = manifest.get("files")
        declared: dict[str, Any] = declared_raw if isinstance(declared_raw, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ["manifest.json is not valid JSON"]

    problems: list[str] = []
    actual = {
        f.rel_path: f.abs_path
        for f in bundle_mod.iter_files(tree)
        if f.rel_path != bundle_mod.MANIFEST_NAME
    }
    for rel_path, meta in sorted(declared.items()):
        if rel_path not in actual:
            problems.append(f"{rel_path}: listed in manifest.json but missing")
            continue
        data = actual[rel_path].read_bytes()
        expected = meta.get("sha256") if isinstance(meta, dict) else None
        if expected and hashlib.sha256(data).hexdigest() != expected:
            problems.append(f"{rel_path}: content does not match manifest.json checksum")
    for rel_path in sorted(set(actual) - set(declared)):
        problems.append(f"{rel_path}: present but not listed in manifest.json")
    return problems


def _lint_summary(tree: Path, today: str | None) -> str:
    report = lint_bundle(tree, LintConfig(today=today or date.today().isoformat()))
    summary = report.to_dict()["summary"]
    return json.dumps(summary, sort_keys=True, separators=(",", ":"))


def install_bundle(
    store: Store,
    catalog: Catalog,
    source: Path,
    *,
    alias: str,
    update: bool = False,
    title: str | None = None,
    description: str | None = None,
    limits: ExtractLimits | None = None,
    today: str | None = None,
) -> tuple[CatalogEntry, ImageRecord, list[str]]:
    """Install a local bundle directory or archive; returns (entry, image, warnings)."""
    warnings: list[str] = []

    if source.is_dir():
        record = build_image_from_tree(store, source, title=title, description=description)
    elif source.is_file():
        _verify_outer_checksum(source, warnings)
        record = build_image_from_archive(
            store, source, title=title, description=description, limits=limits
        )
    else:
        raise InstallError(f"bundle source not found: {source}")

    tree = store.tree_path(record.layer_digest)
    problems = verify_bundle_manifest(tree)
    if problems:
        detail = "; ".join(problems[:5]) + ("; …" if len(problems) > 5 else "")
        raise InstallError(f"bundle manifest integrity check failed: {detail}")
    if not (tree / bundle_mod.MANIFEST_NAME).is_file():
        warnings.append("bundle carries no manifest.json — content integrity is unverified")

    entry = CatalogEntry(
        alias=alias,
        mode="local",
        reference=source.resolve().as_uri().replace("file://", "file:", 1),
        oci_digest=record.manifest_digest,
        okf_revision=record.revision,
        title=record.title,
        description=record.description,
        lint_summary=_lint_summary(tree, today),
    )
    catalog.register(entry, update=update)
    return catalog.get(alias) or entry, record, warnings


def _verify_outer_checksum(archive: Path, warnings: list[str]) -> None:
    """Verify the ``<archive>.sha256`` sidecar when present (packer writes one)."""
    sidecar = archive.with_name(archive.name + ".sha256")
    if not sidecar.is_file():
        return
    declared = sidecar.read_text(encoding="utf-8").split()
    if not declared:
        warnings.append(f"unreadable checksum sidecar {sidecar.name} — ignored")
        return
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if declared[0] != actual:
        raise InstallError(
            f"archive does not match its checksum sidecar {sidecar.name} "
            f"(expected {declared[0][:12]}…, got {actual[:12]}…)"
        )


def checkout_bundle(store: Store, entry: CatalogEntry, dest: Path) -> Path:
    """Materialize a human-visible working tree pinned to the installed revision."""
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise InstallError(f"checkout destination is not an empty directory: {dest}")
    manifest = store.read_manifest(entry.oci_digest)
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers or not isinstance(layers[0], dict):
        raise InstallError(f"malformed manifest for {entry.alias!r}")
    tree = store.tree_path(str(layers[0].get("digest", "")))
    if not tree.is_dir():
        raise InstallError(f"content tree for {entry.alias!r} is missing from the store")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        publish_revision(dest, tree, entry.okf_revision)
        # The copies inherit the store tree's write protection; a checkout is
        # the human-visible working tree, so hand it back writable.
        write_unprotect_tree(dest)
        origin = {
            "alias": entry.alias,
            "reference": entry.reference,
            "oci_digest": entry.oci_digest,
            "okf_revision": entry.okf_revision,
        }
        origin_path = dest / bundle_mod.DERIVED_STATE_DIR / "origin.json"
        origin_path.write_text(
            json.dumps(origin, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest

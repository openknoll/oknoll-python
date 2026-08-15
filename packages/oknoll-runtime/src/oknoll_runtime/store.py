"""Machine-level content-addressed store for OkNoll images.

Layout under the runtime data directory:

- ``blobs/sha256/<hex>`` — raw blobs (image configs and bundle layers);
- ``manifests/sha256/<hex>`` — OCI image manifests, also content-addressed;
- ``trees/sha256-<hex>/`` — bundle trees extracted from a layer, keyed by the
  **layer** digest so identical payloads deduplicate across images.

Everything published is immutable: blobs and manifests are written to a
temporary sibling and renamed into place read-only; trees are renamed in as a
whole directory and then write-protected. Publishing something that already
exists is a no-op — content addressing makes the second copy byte-identical
by construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

DIGEST_PREFIX = "sha256:"


class StoreError(RuntimeError):
    pass


def format_digest(hex_digest: str) -> str:
    return f"{DIGEST_PREFIX}{hex_digest}"


def digest_hex(digest: str) -> str:
    """The hex part of a ``sha256:<64-hex>`` digest, validated."""
    if not digest.startswith(DIGEST_PREFIX):
        raise StoreError(f"not a sha256 digest: {digest!r}")
    hex_part = digest[len(DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(c not in "0123456789abcdef" for c in hex_part):
        raise StoreError(f"malformed digest hex: {digest!r}")
    return hex_part


def sha256_digest(data: bytes) -> str:
    return format_digest(hashlib.sha256(data).hexdigest())


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir

    # -- blobs -------------------------------------------------------------

    def blob_path(self, digest: str) -> Path:
        return self.root / "blobs" / "sha256" / digest_hex(digest)

    def put_blob(self, data: bytes) -> str:
        digest = sha256_digest(data)
        self._write_immutable(self.blob_path(digest), data)
        return digest

    def has_blob(self, digest: str) -> bool:
        return self.blob_path(digest).is_file()

    def read_blob(self, digest: str) -> bytes:
        path = self.blob_path(digest)
        if not path.is_file():
            raise StoreError(f"blob not in store: {digest}")
        data = path.read_bytes()
        if sha256_digest(data) != digest:
            raise StoreError(f"store corruption: blob bytes do not match {digest}")
        return data

    # -- manifests ---------------------------------------------------------

    def manifest_path(self, digest: str) -> Path:
        return self.root / "manifests" / "sha256" / digest_hex(digest)

    def put_manifest(self, data: bytes) -> str:
        digest = sha256_digest(data)
        self._write_immutable(self.manifest_path(digest), data)
        return digest

    def read_manifest(self, digest: str) -> dict[str, Any]:
        path = self.manifest_path(digest)
        if not path.is_file():
            raise StoreError(f"image not in store: {digest}")
        data = path.read_bytes()
        if sha256_digest(data) != digest:
            raise StoreError(f"store corruption: manifest bytes do not match {digest}")
        loaded = json.loads(data)
        if not isinstance(loaded, dict):
            raise StoreError(f"store corruption: manifest {digest} is not a JSON object")
        return loaded

    def read_manifest_bytes(self, digest: str) -> bytes:
        path = self.manifest_path(digest)
        if not path.is_file():
            raise StoreError(f"image not in store: {digest}")
        return path.read_bytes()

    def has_manifest(self, digest: str) -> bool:
        return self.manifest_path(digest).is_file()

    def list_manifest_digests(self) -> list[str]:
        directory = self.root / "manifests" / "sha256"
        if not directory.is_dir():
            return []
        return sorted(format_digest(p.name) for p in directory.iterdir() if p.is_file())

    # -- trees -------------------------------------------------------------

    def tree_path(self, layer_digest: str) -> Path:
        return self.root / "trees" / f"sha256-{digest_hex(layer_digest)}"

    def has_tree(self, layer_digest: str) -> bool:
        return self.tree_path(layer_digest).is_dir()

    def publish_tree(self, staged: Path, layer_digest: str) -> Path:
        """Atomically move ``staged`` into the store and write-protect it."""
        target = self.tree_path(layer_digest)
        if target.is_dir():
            shutil.rmtree(staged, ignore_errors=True)  # identical content by CAS
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staged, target)
        write_protect_tree(target)
        return target

    def remove_tree(self, layer_digest: str) -> None:
        target = self.tree_path(layer_digest)
        if target.is_dir():
            write_unprotect_tree(target)
            shutil.rmtree(target)

    # -- removal -----------------------------------------------------------

    def remove_image(self, manifest_digest: str) -> None:
        """Remove a manifest and any blobs/trees no other manifest references."""
        manifest = self.read_manifest(manifest_digest)
        doomed = _referenced_digests(manifest)
        layer_digests = {
            str(layer.get("digest", ""))
            for layer in manifest.get("layers", [])
            if isinstance(layer, dict)
        }

        path = self.manifest_path(manifest_digest)
        path.chmod(0o644)
        path.unlink()

        still_referenced: set[str] = set()
        for digest in self.list_manifest_digests():
            still_referenced |= _referenced_digests(self.read_manifest(digest))

        for digest in doomed - still_referenced:
            blob = self.blob_path(digest)
            if blob.is_file():
                blob.chmod(0o644)
                blob.unlink()
            if digest in layer_digests:
                self.remove_tree(digest)

    # -- plumbing ----------------------------------------------------------

    def _write_immutable(self, path: Path, data: bytes) -> None:
        if path.is_file():
            return  # content-addressed: an existing entry is the same bytes
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.chmod(0o444)
        tmp.replace(path)


def _referenced_digests(manifest: dict[str, Any]) -> set[str]:
    digests: set[str] = set()
    config = manifest.get("config")
    if isinstance(config, dict) and config.get("digest"):
        digests.add(str(config["digest"]))
    for layer in manifest.get("layers", []):
        if isinstance(layer, dict) and layer.get("digest"):
            digests.add(str(layer["digest"]))
    return digests


def write_protect_tree(tree: Path) -> None:
    for current, dirnames, filenames in os.walk(tree, topdown=False):
        for name in filenames:
            (Path(current) / name).chmod(0o444)
        for name in dirnames:
            (Path(current) / name).chmod(0o555)
    tree.chmod(0o555)


def write_unprotect_tree(tree: Path) -> None:
    tree.chmod(0o755)
    for current, dirnames, filenames in os.walk(tree):
        for name in dirnames:
            (Path(current) / name).chmod(0o755)
        for name in filenames:
            (Path(current) / name).chmod(0o644)


def is_write_protected(path: Path) -> bool:
    return not path.stat().st_mode & stat.S_IWUSR

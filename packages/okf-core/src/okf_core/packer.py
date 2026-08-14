"""Deterministic bundle packer.

Same bundle bytes in → same archive bytes out: sorted members, zeroed
timestamps/ownership, normalized modes, gzip with mtime=0. The ``plain``
profile strips OKF-specific frontmatter fields (the hedge against standard
churn) and recomputes the manifest so checksums match the transformed files.
Derived state (``.oknoll/``) is never packed.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path

from okf_core import bundle as bundle_mod
from okf_core.frontmatter import (
    Frontmatter,
    FrontmatterError,
    ParsedDocument,
    parse_document,
    write_document,
)

PROFILES: tuple[str, ...] = ("okf", "plain")

# Frontmatter keys that carry OKF machinery rather than plain document content.
_OKF_ONLY_KEYS: frozenset[str] = frozenset(
    {"okf_version", "type", "status", "generated", "verified", "sources", "stale_after"}
)


@dataclass(frozen=True, slots=True)
class PackResult:
    archive_path: Path
    checksum_path: Path
    sha256: str
    file_count: int


def strip_okf_fields(text: str) -> str:
    """The plain-profile transform for one Markdown document."""
    try:
        doc = parse_document(text)
    except FrontmatterError:
        return text  # permissive: pack foreign/broken files verbatim
    if doc.frontmatter is None:
        return text
    kept = {
        key: value
        for key, value in doc.frontmatter.data.items()
        if key not in _OKF_ONLY_KEYS and not key.startswith("openknoll_")
    }
    return write_document(ParsedDocument(Frontmatter(kept), doc.body))


def _collect(bundle_dir: Path, profile: str) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    for file in bundle_mod.iter_files(bundle_dir):
        data = file.abs_path.read_bytes()
        if profile == "plain" and file.rel_path.endswith(".md"):
            with contextlib.suppress(UnicodeDecodeError):  # not text: pack verbatim
                data = strip_okf_fields(data.decode("utf-8")).encode("utf-8")
        entries.append((file.rel_path, data))

    if profile == "plain":
        files = {
            rel: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for rel, data in entries
            if rel != bundle_mod.MANIFEST_NAME
        }
        manifest = json.dumps({"files": dict(sorted(files.items()))}, indent=2) + "\n"
        entries = [
            (rel, manifest.encode("utf-8") if rel == bundle_mod.MANIFEST_NAME else data)
            for rel, data in entries
        ]
    return sorted(entries)


def pack_bundle(
    bundle_dir: Path,
    archive_path: Path,
    *,
    profile: str = "okf",
    member_root: str | None = None,
) -> PackResult:
    """Write a deterministic tar.gz of the bundle plus a sha256 checksum file."""
    if profile not in PROFILES:
        raise ValueError(f"unknown pack profile {profile!r} (expected one of {PROFILES})")
    if not bundle_dir.is_dir():
        raise ValueError(f"{bundle_dir} is not a bundle directory")

    # Derived member root strips every archive spelling: .tar.gz, .tar,
    # .tgz, and the canonical .okf.tgz.
    root = member_root or (
        archive_path.name.removesuffix(".gz")
        .removesuffix(".tar")
        .removesuffix(".tgz")
        .removesuffix(".okf")
    )
    entries = _collect(bundle_dir, profile)

    buffer = io.BytesIO()
    with (
        gzip.GzipFile(filename="", fileobj=buffer, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for rel_path, data in entries:
            info = tarfile.TarInfo(name=f"{root}/{rel_path}")
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))

    archive_bytes = buffer.getvalue()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_bytes(archive_bytes)

    digest = hashlib.sha256(archive_bytes).hexdigest()
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")

    return PackResult(
        archive_path=archive_path,
        checksum_path=checksum_path,
        sha256=digest,
        file_count=len(entries),
    )

"""Safe extraction of OKF bundle archives.

A bundle archive is untrusted input: it may come from a registry, a download,
or a colleague's laptop. Extraction therefore never trusts member metadata —
paths are validated component by component, only regular files and directories
are accepted (no symlinks, hard links, or device nodes), permission bits are
normalized rather than honored, and compressed/expanded/count/per-file limits
bound resource use. Members land in a private staging directory next to the
destination; after a containment re-validation the single unambiguous bundle
root is published atomically with one rename. On any rejection the destination
is never created.

The archive formats accepted are exactly the ones ``pack_bundle`` can produce
plus their uncompressed form: gzip-compressed and plain POSIX/GNU tar.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

_COPY_CHUNK = 1024 * 1024

DEFAULT_MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_COUNT = 50_000
DEFAULT_MAX_MEMBER_BYTES = 128 * 1024 * 1024


class ExtractError(ValueError):
    """A rejected archive. ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractLimits:
    max_compressed_bytes: int = DEFAULT_MAX_COMPRESSED_BYTES
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES
    max_file_count: int = DEFAULT_MAX_FILE_COUNT
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES


@dataclass(frozen=True, slots=True)
class ExtractResult:
    dest: Path
    file_count: int
    expanded_bytes: int


def _member_path(name: str) -> list[str]:
    """Validate one member name; return its clean path components."""
    if "\\" in name or "\x00" in name:
        raise ExtractError("unsafe_path", f"member name contains forbidden characters: {name!r}")
    if name.startswith("/"):
        raise ExtractError("absolute_path", f"member has an absolute path: {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ExtractError("path_traversal", f"member path traverses upward: {name!r}")
    return parts


def _reject_member_type(member: tarfile.TarInfo) -> None:
    if member.issym():
        raise ExtractError("symlink_member", f"symlink member rejected: {member.name!r}")
    if member.islnk():
        raise ExtractError("hardlink_member", f"hard-link member rejected: {member.name!r}")
    if member.ischr() or member.isblk():
        raise ExtractError("device_member", f"device-node member rejected: {member.name!r}")
    if not (member.isreg() or member.isdir()):
        raise ExtractError(
            "unsupported_member_type",
            f"unsupported member type {member.type!r}: {member.name!r}",
        )


def _open_archive(archive: Path) -> tarfile.TarFile:
    for mode in ("r:gz", "r:"):
        try:
            return tarfile.open(archive, mode=mode)
        except tarfile.ReadError:
            continue
    raise ExtractError("not_an_archive", f"not a tar or tar.gz archive: {archive.name}")


def _extract_members(tar: tarfile.TarFile, staging: Path, limits: ExtractLimits) -> tuple[int, int]:
    """Stream members into ``staging`` under the limits; returns (files, bytes)."""
    seen: set[str] = set()
    member_count = 0
    file_count = 0
    expanded_bytes = 0
    staging_real = staging.resolve()

    for member in tar:
        _reject_member_type(member)
        parts = _member_path(member.name)
        if not parts:
            continue  # the archive's own "./" root entry
        member_count += 1
        if member_count > limits.max_file_count:
            raise ExtractError("too_many_files", f"archive exceeds {limits.max_file_count} members")
        rel = "/".join(parts)
        if rel in seen:  # covers dir/file name collisions too, not just repeats
            raise ExtractError("duplicate_member", f"duplicate member overwrites: {rel!r}")
        seen.add(rel)

        target = staging.joinpath(*parts)
        # Belt and braces: the component check above already forbids escapes.
        if not target.resolve().is_relative_to(staging_real):
            raise ExtractError("containment_violation", f"member escapes staging: {rel!r}")

        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
            continue

        if member.size > limits.max_member_bytes:
            raise ExtractError(
                "member_too_large",
                f"member {rel!r} exceeds {limits.max_member_bytes} bytes",
            )
        file_count += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:  # pragma: no cover - isreg() guarantees a stream
            raise ExtractError("unsupported_member_type", f"unreadable member: {rel!r}")
        with source, target.open("wb") as sink:
            while True:
                chunk = source.read(_COPY_CHUNK)
                if not chunk:
                    break
                expanded_bytes += len(chunk)
                if expanded_bytes > limits.max_expanded_bytes:
                    raise ExtractError(
                        "expanded_too_large",
                        f"archive expands past {limits.max_expanded_bytes} bytes",
                    )
                sink.write(chunk)
        target.chmod(0o644)

    return file_count, expanded_bytes


def _revalidate_containment(staging: Path) -> None:
    """No symlinks may exist in the extracted tree, however they got there."""
    staging_real = staging.resolve()
    for current, dirnames, filenames in os.walk(staging):
        for name in (*dirnames, *filenames):
            path = Path(current) / name
            if path.is_symlink() or not path.resolve().is_relative_to(staging_real):
                raise ExtractError(
                    "containment_violation",
                    f"extracted entry escapes the staging directory: {name!r}",
                )


def _bundle_root(staging: Path) -> Path:
    """The one unambiguous bundle root, or a rejection.

    Accepted shapes: every member under a single top-level directory (the
    ``pack_bundle`` layout — that directory is the root), or bundle files
    directly at the archive top level marked by ``index.md``.
    """
    entries = sorted(staging.iterdir())
    if not entries:
        raise ExtractError("empty_archive", "archive contains no bundle files")
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    if (staging / "index.md").is_file():
        return staging
    raise ExtractError(
        "ambiguous_root",
        "archive has no unambiguous bundle root "
        "(expected one top-level directory, or index.md at the top level)",
    )


def safe_extract_bundle(
    archive: Path, dest: Path, limits: ExtractLimits | None = None
) -> ExtractResult:
    """Extract an untrusted bundle archive to ``dest``, or reject it.

    ``dest`` must not exist yet; it appears atomically (single rename from a
    private staging sibling) only after every check has passed.
    """
    limits = limits or ExtractLimits()
    if not archive.is_file():
        raise ExtractError("archive_not_found", f"archive not found: {archive}")
    if archive.stat().st_size > limits.max_compressed_bytes:
        raise ExtractError(
            "archive_too_large",
            f"archive exceeds {limits.max_compressed_bytes} compressed bytes",
        )
    if dest.exists():
        raise ExtractError("dest_exists", f"destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.extract-", dir=dest.parent))
    try:
        with _open_archive(archive) as tar:
            file_count, expanded_bytes = _extract_members(tar, staging, limits)
        _revalidate_containment(staging)
        root = _bundle_root(staging)
        if not any(root.iterdir()):
            raise ExtractError("empty_archive", "archive contains no bundle files")
        os.rename(root, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return ExtractResult(dest=dest, file_count=file_count, expanded_bytes=expanded_bytes)

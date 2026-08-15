#!/usr/bin/env python3
"""Regenerate the malicious-archive corpus in fixtures/security/archives/.

The corpus is committed; this script exists so the fixtures are reproducible
and reviewable. Each archive encodes one ingestion attack; expected.json maps
every fixture to the ExtractError code safe_extract_bundle must reject it
with. Resource-limit cases (bomb, oversized member, member flood) are sized
against the tightened limits pinned in test_extract_corpus.py so the fixtures
stay tiny — the enforcement mechanism is identical at the default limits.

Run from the repo root: python scripts/gen_security_archives.py
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "security" / "archives"

# filename -> (expected ExtractError code, builder)
EXPECTED: dict[str, str] = {}


def _member(
    name: str,
    data: bytes = b"",
    *,
    type_: bytes = tarfile.REGTYPE,
    linkname: str = "",
    devmajor: int = 0,
) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    info.type = type_
    info.linkname = linkname
    info.devmajor = devmajor
    return info, data


def _write_tar(
    path: Path, members: list[tuple[tarfile.TarInfo, bytes]], *, gz: bool = False
) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if info.isreg() else None)
    raw = buffer.getvalue()
    if gz:
        out = io.BytesIO()
        with gzip.GzipFile(filename="", fileobj=out, mode="wb", mtime=0) as stream:
            stream.write(raw)
        raw = out.getvalue()
    path.write_bytes(raw)


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = b"# evil\n"

    cases: dict[str, tuple[str, list[tuple[tarfile.TarInfo, bytes]]]] = {
        "traversal.tar": (
            "path_traversal",
            [_member("bundle/../../escape.md", doc)],
        ),
        "absolute-path.tar": (
            "absolute_path",
            [_member("/etc/oknoll-owned.md", doc)],
        ),
        "backslash-path.tar": (
            "unsafe_path",
            [_member("bundle\\..\\escape.md", doc)],
        ),
        "symlink-escape.tar": (
            "symlink_member",
            [
                _member("bundle/index.md", doc),
                _member("bundle/out", type_=tarfile.SYMTYPE, linkname="/etc"),
                _member("bundle/out/owned.md", doc),
            ],
        ),
        "symlink-internal.tar": (
            "symlink_member",
            [
                _member("bundle/index.md", doc),
                _member("bundle/alias.md", type_=tarfile.SYMTYPE, linkname="index.md"),
            ],
        ),
        "hardlink-escape.tar": (
            "hardlink_member",
            [
                _member("bundle/index.md", doc),
                _member("bundle/passwd", type_=tarfile.LNKTYPE, linkname="/etc/passwd"),
            ],
        ),
        "device-node.tar": (
            "device_member",
            [
                _member("bundle/index.md", doc),
                _member("bundle/sda", type_=tarfile.BLKTYPE, devmajor=8),
            ],
        ),
        "fifo.tar": (
            "unsupported_member_type",
            [
                _member("bundle/index.md", doc),
                _member("bundle/pipe", type_=tarfile.FIFOTYPE),
            ],
        ),
        "duplicate-entry.tar": (
            "duplicate_member",
            [
                _member("bundle/index.md", doc),
                _member("bundle/index.md", b"# overwritten\n"),
            ],
        ),
        "dir-file-collision.tar": (
            "duplicate_member",
            [
                _member("bundle/notes", doc),
                _member("bundle/notes", type_=tarfile.DIRTYPE),
            ],
        ),
        "multi-root.tar": (
            "ambiguous_root",
            [
                _member("bundle-a/index.md", doc),
                _member("bundle-b/index.md", doc),
            ],
        ),
        "no-root-marker.tar": (
            "ambiguous_root",
            [
                _member("readme.txt", doc),
                _member("notes.md", doc),
            ],
        ),
        "empty.tar": ("empty_archive", []),
        "dirs-only.tar": (
            "empty_archive",
            [_member("bundle", type_=tarfile.DIRTYPE)],
        ),
        # Sized against the tightened corpus limits (see test_extract_corpus.py):
        # expanded > 1 MiB total, member > 256 KiB, members > 64.
        "expansion-bomb.tar.gz": (
            "expanded_too_large",
            [_member(f"bundle/zeros-{i}.bin", b"\x00" * (256 * 1024 - 1)) for i in range(8)]
            + [_member("bundle/index.md", doc)],
        ),
        "oversized-member.tar.gz": (
            "member_too_large",
            [_member("bundle/big.bin", b"\x00" * (256 * 1024 + 1))],
        ),
        "member-flood.tar": (
            "too_many_files",
            [_member(f"bundle/f{i:03}.md", doc) for i in range(65)],
        ),
    }

    expected: dict[str, str] = {}
    for filename, (code, members) in sorted(cases.items()):
        _write_tar(OUT / filename, members, gz=filename.endswith(".gz"))
        expected[filename] = code

    # Not a tar at all — rejected before any member is read.
    (OUT / "not-an-archive.bin").write_bytes(b"\x1f\x8b\x00garbage that is neither tar nor gzip")
    expected["not-an-archive.bin"] = "not_an_archive"

    (OUT / "expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(expected)} corpus archives to {OUT}")


if __name__ == "__main__":
    build()

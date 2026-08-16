"""Safe archive extraction: round trips, checksum sidecars, hostile members."""

import io
import tarfile
from pathlib import Path

import pytest
from okf_core import pack_bundle
from okf_core.packer import ArchiveError, extract_archive, verify_archive_checksum
from okf_core.revision import compare_trees


def _hostile_tar(path: Path, members: list[tarfile.TarInfo], data: bytes = b"evil") -> Path:
    with tarfile.open(path, mode="w:gz") as tar:
        for info in members:
            if info.type == tarfile.REGTYPE:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                tar.addfile(info)
    return path


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


def test_pack_extract_round_trip(golden_dir: Path, tmp_path: Path) -> None:
    bundle = golden_dir / "minimal"
    result = pack_bundle(bundle, tmp_path / "minimal.tar.gz", member_root="m-rev")

    extracted = extract_archive(result.archive_path, tmp_path / "out")
    assert extracted == tmp_path / "out" / "m-rev"  # member_root stripped
    assert compare_trees(bundle, extracted) == []


def test_multi_root_archive_returns_dest_dir(tmp_path: Path) -> None:
    top_a = tarfile.TarInfo(name="a.md")
    top_b = tarfile.TarInfo(name="b.md")
    archive = _hostile_tar(tmp_path / "flat.tar.gz", [top_a, top_b])

    extracted = extract_archive(archive, tmp_path / "out")
    assert extracted == tmp_path / "out"
    assert (extracted / "a.md").is_file() and (extracted / "b.md").is_file()


def test_checksum_sidecar_verifies(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(golden_dir / "minimal", tmp_path / "m.tar.gz")
    assert verify_archive_checksum(result.archive_path) == result.sha256


def test_checksum_mismatch_is_rejected(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(golden_dir / "minimal", tmp_path / "m.tar.gz")
    result.archive_path.write_bytes(result.archive_path.read_bytes() + b"\x00")
    with pytest.raises(ArchiveError, match="checksum mismatch"):
        verify_archive_checksum(result.archive_path)


def test_unparseable_sidecar_is_rejected(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(golden_dir / "minimal", tmp_path / "m.tar.gz")
    result.checksum_path.write_text("what even is this\n", encoding="utf-8")
    with pytest.raises(ArchiveError, match="unparseable checksum"):
        verify_archive_checksum(result.archive_path)


def test_missing_sidecar_returns_none(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(golden_dir / "minimal", tmp_path / "m.tar.gz")
    result.checksum_path.unlink()
    assert verify_archive_checksum(result.archive_path) is None


def test_traversal_member_is_rejected(tmp_path: Path) -> None:
    archive = _hostile_tar(tmp_path / "evil.tar.gz", [tarfile.TarInfo(name="../evil.md")])
    with pytest.raises(ArchiveError):
        extract_archive(archive, tmp_path / "dest")
    assert not (tmp_path / "evil.md").exists()


def test_absolute_member_is_defanged(tmp_path: Path) -> None:
    # The data filter strips leading slashes: the member lands inside dest,
    # never at its absolute path.
    outside = tmp_path / "outside" / "evil.md"
    archive = _hostile_tar(tmp_path / "abs.tar.gz", [tarfile.TarInfo(name=str(outside))])
    dest = tmp_path / "dest"
    extract_archive(archive, dest)
    assert not outside.exists()
    assert (dest / str(outside).lstrip("/")).is_file()


def test_escaping_symlink_is_rejected(tmp_path: Path) -> None:
    archive = _hostile_tar(
        tmp_path / "link.tar.gz",
        [_symlink("esc", "../.."), tarfile.TarInfo(name="esc/evil.md")],
    )
    with pytest.raises(ArchiveError):
        extract_archive(archive, tmp_path / "dest")
    assert not (tmp_path / "evil.md").exists()


def test_truncated_archive_is_rejected(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(golden_dir / "minimal", tmp_path / "m.tar.gz")
    truncated = tmp_path / "trunc.tar.gz"
    truncated.write_bytes(result.archive_path.read_bytes()[:40])
    with pytest.raises(ArchiveError):
        extract_archive(truncated, tmp_path / "dest")


def test_not_gzip_is_rejected(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.tar.gz"
    bogus.write_bytes(b"this is not a gzip stream")
    with pytest.raises(ArchiveError):
        extract_archive(bogus, tmp_path / "dest")

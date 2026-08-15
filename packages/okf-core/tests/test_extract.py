"""safe_extract_bundle happy paths: pack → extract round-trips, root shapes, modes."""

import io
import tarfile
from pathlib import Path

import pytest
from okf_core import ExtractError, ExtractLimits, pack_bundle, safe_extract_bundle
from okf_core.revision import compare_trees

GOLDEN = Path(__file__).resolve().parents[3] / "fixtures" / "bundles" / "golden" / "minimal"


def test_pack_then_extract_round_trips_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "minimal.okf.tgz"
    pack_bundle(GOLDEN, archive)
    result = safe_extract_bundle(archive, tmp_path / "out")

    assert result.dest == tmp_path / "out"
    assert result.file_count > 0
    assert compare_trees(GOLDEN, result.dest) == []


def test_extract_accepts_uncompressed_tar(tmp_path: Path) -> None:
    archive = tmp_path / "plain.tar"
    with tarfile.open(archive, mode="w") as tar:
        info = tarfile.TarInfo(name="bundle/index.md")
        data = b"# hello\n"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    result = safe_extract_bundle(archive, tmp_path / "out")
    assert (result.dest / "index.md").read_bytes() == data


def test_extract_accepts_top_level_index_root(tmp_path: Path) -> None:
    """No wrapping directory: index.md at the archive top marks the root."""
    archive = tmp_path / "flat.tar"
    with tarfile.open(archive, mode="w") as tar:
        for name in ("index.md", "concepts/one.md"):
            info = tarfile.TarInfo(name=name)
            info.size = 2
            tar.addfile(info, io.BytesIO(b"# "))
    result = safe_extract_bundle(archive, tmp_path / "out")
    assert (result.dest / "index.md").is_file()
    assert (result.dest / "concepts" / "one.md").is_file()


def test_extract_normalizes_modes_and_counts_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "modes.tar"
    with tarfile.open(archive, mode="w") as tar:
        info = tarfile.TarInfo(name="bundle/tool.md")
        info.mode = 0o777  # setuid-adjacent modes are never honored
        data = b"# not executable\n"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    result = safe_extract_bundle(archive, tmp_path / "out")
    assert (result.dest / "tool.md").stat().st_mode & 0o777 == 0o644
    assert result.expanded_bytes == len(b"# not executable\n")


def test_extract_refuses_existing_destination(tmp_path: Path) -> None:
    archive = tmp_path / "minimal.okf.tgz"
    pack_bundle(GOLDEN, archive)
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(ExtractError) as excinfo:
        safe_extract_bundle(archive, dest)
    assert excinfo.value.code == "dest_exists"


def test_extract_refuses_missing_archive(tmp_path: Path) -> None:
    with pytest.raises(ExtractError) as excinfo:
        safe_extract_bundle(tmp_path / "nope.tgz", tmp_path / "out")
    assert excinfo.value.code == "archive_not_found"


def test_extract_enforces_compressed_size_before_reading(tmp_path: Path) -> None:
    archive = tmp_path / "minimal.okf.tgz"
    pack_bundle(GOLDEN, archive)
    limits = ExtractLimits(max_compressed_bytes=8)
    with pytest.raises(ExtractError) as excinfo:
        safe_extract_bundle(archive, tmp_path / "out", limits)
    assert excinfo.value.code == "archive_too_large"


def test_default_limits_are_sane() -> None:
    limits = ExtractLimits()
    assert limits.max_compressed_bytes <= limits.max_expanded_bytes
    assert limits.max_member_bytes <= limits.max_expanded_bytes
    assert limits.max_file_count >= 10_000

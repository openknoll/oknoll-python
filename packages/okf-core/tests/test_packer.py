"""Deterministic packer: identical bytes, correct profiles, honest checksums."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from okf_core import pack_bundle, parse_document


def _members(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive, mode="r:gz") as tar:
        return {
            m.name: tar.extractfile(m).read()  # type: ignore[union-attr]
            for m in tar.getmembers()
        }


def test_pack_is_byte_deterministic(golden_dir: Path, tmp_path: Path) -> None:
    bundle = golden_dir / "minimal"
    first = pack_bundle(bundle, tmp_path / "a" / "minimal.tar.gz")
    second = pack_bundle(bundle, tmp_path / "b" / "minimal.tar.gz")
    assert first.sha256 == second.sha256
    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()

    checksum_line = first.checksum_path.read_text(encoding="utf-8")
    assert checksum_line == f"{first.sha256}  minimal.tar.gz\n"
    assert hashlib.sha256(first.archive_path.read_bytes()).hexdigest() == first.sha256


def test_pack_okf_profile_is_verbatim_and_normalized(golden_dir: Path, tmp_path: Path) -> None:
    bundle = golden_dir / "minimal"
    result = pack_bundle(bundle, tmp_path / "minimal.tar.gz", member_root="minimal")
    members = _members(tmp_path / "minimal.tar.gz")
    assert result.file_count == len(members)
    assert sorted(members) == list(members)  # sorted member order
    assert members["minimal/index.md"] == (bundle / "index.md").read_bytes()

    with tarfile.open(tmp_path / "minimal.tar.gz", mode="r:gz") as tar:
        for member in tar.getmembers():
            assert member.mtime == 0
            assert member.uid == 0 and member.gid == 0
            assert member.uname == "" and member.gname == ""


def test_pack_plain_profile_strips_okf_fields(golden_dir: Path, tmp_path: Path) -> None:
    bundle = golden_dir / "minimal"
    pack_bundle(bundle, tmp_path / "plain.tar.gz", profile="plain", member_root="minimal")
    members = _members(tmp_path / "plain.tar.gz")

    concept = parse_document(members["minimal/concepts/authentication.md"].decode("utf-8"))
    assert concept.frontmatter is not None
    data = concept.frontmatter.data
    for stripped in ("type", "status", "generated", "sources", "okf_version", "stale_after"):
        assert stripped not in data
    assert data["title"] == "Authentication architecture"
    assert "description" in data and "tags" in data
    assert "session boundary" in concept.body  # body is untouched

    manifest = json.loads(members["minimal/manifest.json"].decode("utf-8"))
    for rel, spec in manifest["files"].items():
        payload = members[f"minimal/{rel}"]
        assert spec["sha256"] == hashlib.sha256(payload).hexdigest(), rel
        assert spec["bytes"] == len(payload)


def test_pack_never_includes_derived_state(golden_dir: Path, tmp_path: Path) -> None:
    bundle_src = golden_dir / "minimal"
    bundle = tmp_path / "bundle"
    import shutil

    shutil.copytree(bundle_src, bundle)
    (bundle / ".oknoll" / "cache").mkdir(parents=True)
    (bundle / ".oknoll" / "cache" / "junk.json").write_text("{}", encoding="utf-8")

    pack_bundle(bundle, tmp_path / "out.tar.gz")
    assert not any(".oknoll" in name for name in _members(tmp_path / "out.tar.gz"))


def test_pack_rejects_unknown_profile(golden_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown pack profile"):
        pack_bundle(golden_dir / "minimal", tmp_path / "x.tar.gz", profile="zip")


# The OCI layer digest of an OkNoll image is over these compressed bytes
# (spec/image-config-v1.md): the same tree must gzip to the same bytes on
# every OS and zlib build. If this pin breaks on one platform, published
# image digests would diverge — that is a release blocker, not a test to
# update casually. (Legitimate fixture/packer changes update the pin.)
GOLDEN_MINIMAL_PACK_SHA256 = "5c2b9574b30e881b4c0004e6f65ddec13da0e8715e1571118d512f5e6dd89c9a"


def test_pack_digest_is_pinned_cross_os(golden_dir: Path, tmp_path: Path) -> None:
    result = pack_bundle(
        golden_dir / "minimal", tmp_path / "minimal.okf.tgz", member_root="minimal"
    )
    assert result.sha256 == GOLDEN_MINIMAL_PACK_SHA256
    assert result.file_count == 6


def test_okf_tgz_archives_derive_a_clean_member_root(golden_dir: Path, tmp_path: Path) -> None:
    pack_bundle(golden_dir / "minimal", tmp_path / "handbook.okf.tgz")
    members = _members(tmp_path / "handbook.okf.tgz")
    assert members and all(name.startswith("handbook/") for name in members)


def test_gzip_stream_has_no_timestamp(golden_dir: Path, tmp_path: Path) -> None:
    pack_bundle(golden_dir / "minimal", tmp_path / "t.tar.gz")
    header = io.BytesIO((tmp_path / "t.tar.gz").read_bytes()).read(8)
    assert header[4:8] == b"\x00\x00\x00\x00"  # gzip MTIME field zeroed

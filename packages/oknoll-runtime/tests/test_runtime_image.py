"""Image build determinism, archive byte preservation, save/load round trip."""

import json
import tarfile
from pathlib import Path

import pytest
from okf_core import ARTIFACT_TYPE, CONFIG_MEDIA_TYPE, LAYER_MEDIA_TYPE, pack_bundle
from okf_core.revision import compute_revision_id
from oknoll_runtime import (
    ImageError,
    Store,
    build_image_from_archive,
    build_image_from_tree,
    load_image,
    read_image_record,
    save_image,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "bundles"
GOLDEN = FIXTURES / "golden" / "minimal"


def test_build_twice_yields_the_same_manifest_digest(tmp_path: Path) -> None:
    store_a = Store(tmp_path / "a")
    store_b = Store(tmp_path / "b")
    first = build_image_from_tree(store_a, GOLDEN)
    second = build_image_from_tree(store_b, GOLDEN)
    assert first.manifest_digest == second.manifest_digest
    assert first.layer_digest == second.layer_digest
    assert first.config_digest == second.config_digest


def test_manifest_shape_and_annotations(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    record = build_image_from_tree(store, GOLDEN)
    manifest = store.read_manifest(record.manifest_digest)

    assert manifest["schemaVersion"] == 2
    assert manifest["artifactType"] == ARTIFACT_TYPE
    assert manifest["config"]["mediaType"] == CONFIG_MEDIA_TYPE
    assert [layer["mediaType"] for layer in manifest["layers"]] == [LAYER_MEDIA_TYPE]
    assert manifest["annotations"]["org.opencontainers.image.title"] == "Minimal golden bundle"
    assert record.title == "Minimal golden bundle"
    assert record.revision == compute_revision_id(GOLDEN)
    assert record.config["bundleRevision"] == record.revision
    # Nothing time-dependent may enter the bytes.
    assert "created" not in json.dumps(manifest)


def test_layer_bytes_are_the_exact_pack_archive(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    record = build_image_from_tree(store, GOLDEN)
    revision = compute_revision_id(GOLDEN)
    expected = pack_bundle(GOLDEN, tmp_path / "ref.okf.tgz", member_root=f"okf-{revision}")
    assert store.read_blob(record.layer_digest) == (tmp_path / "ref.okf.tgz").read_bytes()
    assert record.layer_digest == f"sha256:{expected.sha256}"


def test_archive_install_preserves_original_bytes(tmp_path: Path) -> None:
    """A foreign archive becomes the layer verbatim — no re-encoding."""
    archive = tmp_path / "foreign.okf.tgz"
    pack_bundle(GOLDEN, archive, member_root="whatever-root")
    store = Store(tmp_path / "data")
    record = build_image_from_archive(store, archive)
    assert store.read_blob(record.layer_digest) == archive.read_bytes()
    assert record.revision == compute_revision_id(GOLDEN)


def test_save_load_round_trip(tmp_path: Path) -> None:
    source = Store(tmp_path / "source")
    record = build_image_from_tree(source, GOLDEN)
    out = tmp_path / "image.tar"
    save_image(source, record.manifest_digest, out, reference="handbook:1.0")

    target = Store(tmp_path / "target")
    loaded = load_image(target, out)
    assert loaded.manifest_digest == record.manifest_digest
    assert loaded.layer_digest == record.layer_digest
    assert loaded.revision == record.revision
    assert target.has_tree(record.layer_digest)
    assert (target.tree_path(record.layer_digest) / "index.md").is_file()


def test_save_is_deterministic(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    record = build_image_from_tree(store, GOLDEN)
    save_image(store, record.manifest_digest, tmp_path / "one.tar")
    save_image(store, record.manifest_digest, tmp_path / "two.tar")
    assert (tmp_path / "one.tar").read_bytes() == (tmp_path / "two.tar").read_bytes()


def test_load_rejects_tampered_layer(tmp_path: Path) -> None:
    store = Store(tmp_path / "source")
    record = build_image_from_tree(store, GOLDEN)
    out = tmp_path / "image.tar"
    save_image(store, record.manifest_digest, out)

    # Flip bytes of the layer blob inside the tar.
    tampered = tmp_path / "tampered.tar"
    with tarfile.open(out) as tar, tarfile.open(tampered, "w", format=tarfile.GNU_FORMAT) as new:
        for member in tar:
            source = tar.extractfile(member)
            assert source is not None
            data = source.read()
            if member.name.endswith(record.layer_digest.removeprefix("sha256:")):
                data = b"\x00" * len(data)
            member.size = len(data)
            import io

            new.addfile(member, io.BytesIO(data))
    with pytest.raises(ImageError, match="do not match"):
        load_image(Store(tmp_path / "target"), tampered)


def test_load_rejects_junk(tmp_path: Path) -> None:
    junk = tmp_path / "junk.tar"
    junk.write_bytes(b"not a tar at all")
    with pytest.raises(ImageError):
        load_image(Store(tmp_path / "target"), junk)


def test_read_image_record_round_trips(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    record = build_image_from_tree(store, GOLDEN)
    reread = read_image_record(store, record.manifest_digest)
    assert reread == record

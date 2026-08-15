"""Content-addressed store: immutability, dedup, reference-counted removal."""

from pathlib import Path

import pytest
from oknoll_runtime import Store, StoreError, sha256_digest
from oknoll_runtime.store import is_write_protected


def test_blob_round_trip_and_immutability(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    digest = store.put_blob(b"hello")
    assert digest == sha256_digest(b"hello")
    assert store.read_blob(digest) == b"hello"
    assert is_write_protected(store.blob_path(digest))
    # Re-putting identical content is a no-op, not an error.
    assert store.put_blob(b"hello") == digest


def test_read_blob_detects_corruption(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    digest = store.put_blob(b"payload")
    path = store.blob_path(digest)
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(StoreError, match="corruption"):
        store.read_blob(digest)


def test_malformed_digests_are_rejected(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    for bad in ("sha256:short", "md5:" + "a" * 64, "a" * 64, "sha256:" + "Z" * 64):
        with pytest.raises(StoreError):
            store.blob_path(bad)


def test_tree_publish_is_atomic_and_write_protected(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    digest = sha256_digest(b"layer")
    staged = tmp_path / "staged"
    (staged / "concepts").mkdir(parents=True)
    (staged / "index.md").write_text("# t\n", encoding="utf-8")
    (staged / "concepts" / "a.md").write_text("# a\n", encoding="utf-8")

    tree = store.publish_tree(staged, digest)
    assert tree == store.tree_path(digest)
    assert not staged.exists()  # moved, not copied
    assert is_write_protected(tree / "index.md")
    assert is_write_protected(tree / "concepts")


def test_remove_image_keeps_shared_blobs(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    shared_layer = store.put_blob(b"shared-layer")
    config_a = store.put_blob(b'{"a":1}')
    config_b = store.put_blob(b'{"b":2}')

    def manifest(config: str) -> str:
        from oknoll_runtime import canonical_json

        return store.put_manifest(
            canonical_json(
                {
                    "schemaVersion": 2,
                    "config": {"digest": config, "size": 7},
                    "layers": [{"digest": shared_layer, "size": 12}],
                }
            )
        )

    digest_a = manifest(config_a)
    digest_b = manifest(config_b)

    store.remove_image(digest_a)
    assert not store.has_manifest(digest_a)
    assert not store.has_blob(config_a)  # only A referenced it
    assert store.has_blob(shared_layer)  # B still does

    store.remove_image(digest_b)
    assert not store.has_blob(shared_layer)
    assert store.list_manifest_digests() == []

"""Install → checkout: integrity gates and the byte-preserving round trip."""

import json
import shutil
from pathlib import Path

import pytest
from okf_core import pack_bundle
from okf_core.revision import compare_trees, compute_revision_id
from oknoll_runtime import (
    Catalog,
    CatalogError,
    InstallError,
    Store,
    checkout_bundle,
    install_bundle,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "bundles"
GOLDEN = FIXTURES / "golden" / "minimal"
FOREIGN = FIXTURES / "upstream" / "acme_retail"


def test_install_directory_and_checkout_round_trip(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    catalog = Catalog(tmp_path / "data")
    entry, record, warnings = install_bundle(
        store, catalog, GOLDEN, alias="handbook", today="2026-08-15"
    )
    assert entry.alias == "handbook"
    assert entry.mode == "local"
    assert entry.oci_digest == record.manifest_digest
    assert entry.okf_revision == compute_revision_id(GOLDEN)
    assert json.loads(entry.lint_summary)["errors"] == 0
    assert warnings == []

    dest = tmp_path / "checkout"
    checkout_bundle(store, entry, dest)
    # Byte-identical content and identical revision id.
    assert compare_trees(GOLDEN, dest) == []
    assert compute_revision_id(dest) == entry.okf_revision
    # Checkout carries local state: pinned revision + provenance, and is writable.
    assert (dest / ".oknoll" / "current").read_text(encoding="utf-8").strip() == (
        entry.okf_revision
    )
    origin = json.loads((dest / ".oknoll" / "origin.json").read_text(encoding="utf-8"))
    assert origin["oci_digest"] == entry.oci_digest
    (dest / "scratch.md").write_text("writable\n", encoding="utf-8")


def test_install_archive_preserves_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "handbook.okf.tgz"
    pack_bundle(GOLDEN, archive)
    store = Store(tmp_path / "data")
    catalog = Catalog(tmp_path / "data")
    entry, record, _ = install_bundle(store, catalog, archive, alias="fromtar", today="2026-08-15")
    assert store.read_blob(record.layer_digest) == archive.read_bytes()
    assert entry.okf_revision == compute_revision_id(GOLDEN)


def test_install_verifies_checksum_sidecar(tmp_path: Path) -> None:
    archive = tmp_path / "handbook.okf.tgz"
    pack_bundle(GOLDEN, archive)  # writes the .sha256 sidecar too
    archive.write_bytes(archive.read_bytes() + b"\x00")  # corrupt after checksumming
    with pytest.raises(InstallError, match="checksum sidecar"):
        install_bundle(
            Store(tmp_path / "data"),
            Catalog(tmp_path / "data"),
            archive,
            alias="bad",
            today="2026-08-15",
        )


def test_install_foreign_bundle_without_manifest_warns_but_succeeds(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    catalog = Catalog(tmp_path / "data")
    entry, _, warnings = install_bundle(store, catalog, FOREIGN, alias="acme", today="2026-08-15")
    assert entry.alias == "acme"
    assert any("manifest.json" in warning for warning in warnings)


def test_install_rejects_tampered_manifest(tmp_path: Path) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(GOLDEN, tampered)
    index = tampered / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")
    with pytest.raises(InstallError, match="integrity"):
        install_bundle(
            Store(tmp_path / "data"),
            Catalog(tmp_path / "data"),
            tampered,
            alias="evil",
            today="2026-08-15",
        )


def test_install_existing_alias_requires_update(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    catalog = Catalog(tmp_path / "data")
    install_bundle(store, catalog, GOLDEN, alias="handbook", today="2026-08-15")
    with pytest.raises(CatalogError, match="--update"):
        install_bundle(store, catalog, GOLDEN, alias="handbook", today="2026-08-15")
    entry, _, _ = install_bundle(
        store, catalog, GOLDEN, alias="handbook", update=True, today="2026-08-15"
    )
    assert entry.alias == "handbook"


def test_checkout_refuses_nonempty_destination(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    catalog = Catalog(tmp_path / "data")
    entry, _, _ = install_bundle(store, catalog, GOLDEN, alias="handbook", today="2026-08-15")
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "occupied.txt").write_text("here first\n", encoding="utf-8")
    with pytest.raises(InstallError, match="empty"):
        checkout_bundle(store, entry, dest)

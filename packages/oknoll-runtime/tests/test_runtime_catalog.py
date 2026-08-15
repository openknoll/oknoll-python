"""Catalog semantics: alias uniqueness, --update repoint, tags, timestamps."""

from pathlib import Path

import pytest
from oknoll_runtime import Catalog, CatalogEntry, CatalogError

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _entry(alias: str = "handbook", digest: str = DIGEST_A) -> CatalogEntry:
    return CatalogEntry(
        alias=alias,
        mode="local",
        reference="file:/tmp/handbook",
        oci_digest=digest,
        okf_revision="rev-" + "0" * 12,
        title="Handbook",
    )


def test_register_get_list_remove(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    catalog.register(_entry())
    stored = catalog.get("handbook")
    assert stored is not None
    assert stored.oci_digest == DIGEST_A
    assert stored.installed_at  # stamped at registration
    assert [e.alias for e in catalog.list_entries()] == ["handbook"]

    catalog.remove("handbook")
    assert catalog.get("handbook") is None
    with pytest.raises(CatalogError, match="no installed bundle"):
        catalog.remove("handbook")


def test_existing_alias_needs_update(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    catalog.register(_entry())
    with pytest.raises(CatalogError, match="--update"):
        catalog.register(_entry(digest=DIGEST_B))
    catalog.register(_entry(digest=DIGEST_B), update=True)
    stored = catalog.get("handbook")
    assert stored is not None and stored.oci_digest == DIGEST_B


def test_alias_and_mode_validation(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    with pytest.raises(CatalogError, match="invalid alias"):
        catalog.register(_entry(alias="Handbook"))
    bad_mode = CatalogEntry(
        alias="x", mode="cloudy", reference="r", oci_digest=DIGEST_A, okf_revision="rev-" + "0" * 12
    )
    with pytest.raises(CatalogError, match="invalid catalog mode"):
        catalog.register(bad_mode)


def test_touch_last_used(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    catalog.register(_entry())
    entry = catalog.get("handbook")
    assert entry is not None and entry.last_used_at is None
    catalog.touch_last_used("handbook")
    touched = catalog.get("handbook")
    assert touched is not None and touched.last_used_at


def test_image_tags(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    catalog.set_tag("handbook", "1.0", DIGEST_A)
    catalog.set_tag("handbook", "latest", DIGEST_A)
    assert catalog.resolve_tag("handbook", "1.0") == DIGEST_A
    assert catalog.resolve_tag("handbook", "missing") is None

    catalog.set_tag("handbook", "latest", DIGEST_B)  # tags are mutable pointers
    assert catalog.resolve_tag("handbook", "latest") == DIGEST_B

    assert catalog.list_tags() == [
        ("handbook", "1.0", DIGEST_A),
        ("handbook", "latest", DIGEST_B),
    ]
    catalog.remove_tags_for(DIGEST_A)
    assert catalog.list_tags() == [("handbook", "latest", DIGEST_B)]

    with pytest.raises(CatalogError, match="invalid image tag"):
        catalog.set_tag("handbook", "no colons:here", DIGEST_A)


def test_digests_in_use(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path)
    catalog.register(_entry())
    catalog.register(_entry(alias="other", digest=DIGEST_B))
    assert catalog.digests_in_use() == {DIGEST_A, DIGEST_B}

"""OpenKnoll local runtime: content store, OkNoll images, catalog, locators.

Everything here is local and offline — registry and cloud clients layer on
top. The store holds immutable content-addressed blobs, manifests, and
extracted bundle trees; the catalog maps mutable aliases to pinned immutable
identities; the locator parser is the one grammar every knowledge-consuming
command shares.
"""

from oknoll_runtime.catalog import Catalog, CatalogEntry, CatalogError
from oknoll_runtime.dirs import RuntimeDirs, runtime_dirs
from oknoll_runtime.image import (
    ImageError,
    ImageRecord,
    build_image_from_archive,
    build_image_from_tree,
    canonical_json,
    load_image,
    read_image_record,
    save_image,
)
from oknoll_runtime.installer import (
    InstallError,
    checkout_bundle,
    install_bundle,
    installed_tree,
    verify_bundle_manifest,
)
from oknoll_runtime.locator import Locator, LocatorError, parse_locator, parse_machine_locator
from oknoll_runtime.store import Store, StoreError, digest_hex, format_digest, sha256_digest

__version__ = "0.3.3"

__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "ImageError",
    "ImageRecord",
    "InstallError",
    "Locator",
    "LocatorError",
    "RuntimeDirs",
    "Store",
    "StoreError",
    "__version__",
    "build_image_from_archive",
    "build_image_from_tree",
    "canonical_json",
    "checkout_bundle",
    "digest_hex",
    "format_digest",
    "install_bundle",
    "installed_tree",
    "load_image",
    "parse_locator",
    "parse_machine_locator",
    "read_image_record",
    "runtime_dirs",
    "save_image",
    "sha256_digest",
    "verify_bundle_manifest",
]

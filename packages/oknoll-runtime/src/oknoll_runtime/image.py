"""OkNoll images: deterministic OCI artifacts wrapping one OKF bundle.

An image is a plain OCI image manifest (spec 1.1 artifact semantics): one
config blob validated against okf-core's image-config v1 contract and one
layer that is the exact byte-deterministic ``pack_bundle`` archive. Building
the same tree twice yields the same manifest digest — nothing time- or
machine-dependent enters the bytes (canonical JSON, revision-derived member
root, no created timestamp).

``save``/``load`` move images as OCI image-layout tars for air-gapped
transfer. A loaded tar is untrusted input: member paths are constrained to
the layout shape, every blob is digest-verified, and the layer passes
``safe_extract_bundle`` before anything is published into the store.
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okf_core import (
    ARTIFACT_TYPE,
    CONFIG_MEDIA_TYPE,
    LAYER_MEDIA_TYPE,
    ExtractError,
    ExtractLimits,
    FrontmatterError,
    pack_bundle,
    parse_document,
    safe_extract_bundle,
    validate_image_config,
)
from okf_core import bundle as bundle_mod
from okf_core.revision import compute_revision_id

from oknoll_runtime.store import Store, StoreError, digest_hex, sha256_digest

OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_LAYOUT_VERSION = "1.0.0"
MINIMUM_OKNOLL_VERSION = "0.4.0"
REF_NAME_ANNOTATION = "org.opencontainers.image.ref.name"

_LAYOUT_NAME_OK = ("oci-layout", "index.json")


class ImageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ImageRecord:
    manifest_digest: str
    config_digest: str
    layer_digest: str
    revision: str
    title: str
    description: str
    config: dict[str, Any]


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def bundle_metadata(tree: Path) -> tuple[str, str]:
    """(title, description) from index.md frontmatter, with honest fallbacks."""
    index = tree / bundle_mod.INDEX_NAME
    title = ""
    description = ""
    if index.is_file():
        try:
            doc = parse_document(index.read_text(encoding="utf-8"))
        except (FrontmatterError, UnicodeDecodeError):
            doc = None
        if doc is not None and doc.frontmatter is not None:
            raw_title = doc.frontmatter.data.get("title")
            raw_description = doc.frontmatter.data.get("description")
            title = raw_title if isinstance(raw_title, str) else ""
            description = raw_description if isinstance(raw_description, str) else ""
        if not title and doc is not None:
            for line in doc.body.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
    return title or "Untitled bundle", description


def _stage_content_tree(source: Path, staging_parent: Path) -> Path:
    """Copy only bundle content (no derived/hidden state), normalized modes."""
    staged = Path(tempfile.mkdtemp(prefix=".tree-", dir=staging_parent))
    for file in bundle_mod.iter_files(source):
        target = staged / file.rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file.abs_path, target)
        target.chmod(0o644)
    return staged


def build_config(tree: Path, *, title: str, description: str, revision: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "schemaVersion": 1,
        "okfVersion": "0.2",
        "title": title,
        "bundleRevision": revision,
        "minimumOknollVersion": MINIMUM_OKNOLL_VERSION,
        "capabilities": ["explore", "ask"],
    }
    if description:
        config["description"] = description
    errors = validate_image_config(config)
    if errors:  # strict producer: our own output must validate
        raise ImageError("invalid image config: " + "; ".join(errors))
    return config


def _build_manifest(
    config_digest: str, config_size: int, layer_digest: str, layer_size: int, config: dict[str, Any]
) -> dict[str, Any]:
    annotations = {"org.opencontainers.image.title": str(config["title"])}
    if config.get("description"):
        annotations["org.opencontainers.image.description"] = str(config["description"])
    return {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": ARTIFACT_TYPE,
        "config": {
            "mediaType": CONFIG_MEDIA_TYPE,
            "digest": config_digest,
            "size": config_size,
        },
        "layers": [
            {
                "mediaType": LAYER_MEDIA_TYPE,
                "digest": layer_digest,
                "size": layer_size,
            }
        ],
        "annotations": annotations,
    }


def _publish(
    store: Store,
    *,
    layer_bytes: bytes,
    content_tree: Path,
    revision: str,
    title: str,
    description: str,
    config_override: dict[str, Any] | None = None,
) -> ImageRecord:
    """Store blobs + manifest + tree; ``content_tree`` is consumed (moved)."""
    layer_digest = store.put_blob(layer_bytes)
    config = config_override or build_config(
        content_tree, title=title, description=description, revision=revision
    )
    config_bytes = canonical_json(config)
    config_digest = store.put_blob(config_bytes)
    manifest = _build_manifest(
        config_digest, len(config_bytes), layer_digest, len(layer_bytes), config
    )
    manifest_bytes = canonical_json(manifest)
    manifest_digest = store.put_manifest(manifest_bytes)
    store.publish_tree(content_tree, layer_digest)
    return ImageRecord(
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        layer_digest=layer_digest,
        revision=revision,
        title=title,
        description=description,
        config=config,
    )


def build_image_from_tree(
    store: Store,
    tree: Path,
    *,
    title: str | None = None,
    description: str | None = None,
) -> ImageRecord:
    """Build a deterministic image from a bundle directory."""
    if not tree.is_dir():
        raise ImageError(f"bundle directory not found: {tree}")
    store.root.mkdir(parents=True, exist_ok=True)
    staged = _stage_content_tree(tree, store.root)
    try:
        revision = compute_revision_id(staged)
        derived_title, derived_description = bundle_metadata(staged)
        final_title = title or derived_title
        final_description = description if description is not None else derived_description

        with tempfile.TemporaryDirectory(prefix=".pack-", dir=store.root) as pack_dir:
            # The member root derives from content only, so identical trees
            # produce identical layer bytes wherever and whenever they build.
            result = pack_bundle(
                staged, Path(pack_dir) / "layer.okf.tgz", member_root=f"okf-{revision}"
            )
            layer_bytes = result.archive_path.read_bytes()

        return _publish(
            store,
            layer_bytes=layer_bytes,
            content_tree=staged,
            revision=revision,
            title=final_title,
            description=final_description,
        )
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def build_image_from_archive(
    store: Store,
    archive: Path,
    *,
    title: str | None = None,
    description: str | None = None,
    limits: ExtractLimits | None = None,
) -> ImageRecord:
    """Build an image from an existing OKF archive, preserving its exact bytes.

    The archive itself becomes the layer — no re-encoding — so the image
    faithfully wraps a foreign bundle. Extraction is the safe path.
    """
    if not archive.is_file():
        raise ImageError(f"archive not found: {archive}")
    store.root.mkdir(parents=True, exist_ok=True)
    extract_parent = Path(tempfile.mkdtemp(prefix=".ingest-", dir=store.root))
    try:
        extracted = extract_parent / "bundle"
        safe_extract_bundle(archive, extracted, limits)
        staged = _stage_content_tree(extracted, store.root)
    finally:
        shutil.rmtree(extract_parent, ignore_errors=True)
    try:
        revision = compute_revision_id(staged)
        derived_title, derived_description = bundle_metadata(staged)
        return _publish(
            store,
            layer_bytes=archive.read_bytes(),
            content_tree=staged,
            revision=revision,
            title=title or derived_title,
            description=description if description is not None else derived_description,
        )
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def read_image_record(store: Store, manifest_digest: str) -> ImageRecord:
    """Reconstruct an ImageRecord for a stored manifest."""
    manifest = store.read_manifest(manifest_digest)
    config_descriptor = manifest.get("config")
    layers = manifest.get("layers")
    if not isinstance(config_descriptor, dict) or not isinstance(layers, list) or not layers:
        raise ImageError(f"malformed manifest {manifest_digest}")
    config_digest = str(config_descriptor.get("digest", ""))
    layer_digest = str(layers[0].get("digest", "")) if isinstance(layers[0], dict) else ""
    config_raw = json.loads(store.read_blob(config_digest))
    config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}
    return ImageRecord(
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        layer_digest=layer_digest,
        revision=str(config.get("bundleRevision", "")),
        title=str(config.get("title", "")),
        description=str(config.get("description", "")),
        config=config,
    )


# -- OCI image layout (save / load) ---------------------------------------


def save_image(
    store: Store, manifest_digest: str, out_path: Path, *, reference: str | None = None
) -> None:
    """Export one image as a deterministic OCI image-layout tar."""
    manifest_bytes = store.read_manifest_bytes(manifest_digest)
    manifest = store.read_manifest(manifest_digest)

    descriptor: dict[str, Any] = {
        "mediaType": OCI_MANIFEST_MEDIA_TYPE,
        "artifactType": ARTIFACT_TYPE,
        "digest": manifest_digest,
        "size": len(manifest_bytes),
    }
    if reference:
        descriptor["annotations"] = {REF_NAME_ANNOTATION: reference}
    index = {"schemaVersion": 2, "manifests": [descriptor]}

    entries: list[tuple[str, bytes]] = [
        ("oci-layout", canonical_json({"imageLayoutVersion": OCI_LAYOUT_VERSION})),
        ("index.json", canonical_json(index)),
        (f"blobs/sha256/{digest_hex(manifest_digest)}", manifest_bytes),
    ]
    for digest in sorted(
        {str(manifest["config"]["digest"])} | {str(layer["digest"]) for layer in manifest["layers"]}
    ):
        entries.append((f"blobs/sha256/{digest_hex(digest)}", store.read_blob(digest)))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name, data in sorted(entries):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buffer.getvalue())


def load_image(store: Store, tar_path: Path, *, limits: ExtractLimits | None = None) -> ImageRecord:
    """Import an OCI image-layout tar, verifying every digest before publish."""
    limits = limits or ExtractLimits()
    if not tar_path.is_file():
        raise ImageError(f"file not found: {tar_path}")
    if tar_path.stat().st_size > limits.max_compressed_bytes:
        raise ImageError(f"image tar exceeds {limits.max_compressed_bytes} bytes")

    store.root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=".load-", dir=store.root))
    try:
        files = _read_layout_tar(tar_path, workdir, limits)
        index = _load_json(files, "index.json")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ImageError("image tar must contain exactly one manifest")
        descriptor = manifests[0]
        if not isinstance(descriptor, dict):
            raise ImageError("malformed image index")
        manifest_digest = str(descriptor.get("digest", ""))
        manifest_bytes = _verified_blob(files, manifest_digest)
        manifest_raw = json.loads(manifest_bytes)
        manifest: dict[str, Any] = manifest_raw if isinstance(manifest_raw, dict) else {}

        config_descriptor = manifest.get("config")
        layers = manifest.get("layers")
        if not isinstance(config_descriptor, dict) or not isinstance(layers, list):
            raise ImageError("malformed image manifest")
        if len(layers) != 1 or not isinstance(layers[0], dict):
            raise ImageError("OkNoll images carry exactly one bundle layer")
        if layers[0].get("mediaType") != LAYER_MEDIA_TYPE:
            raise ImageError(f"unexpected layer media type {layers[0].get('mediaType')!r}")

        config_bytes = _verified_blob(files, str(config_descriptor.get("digest", "")))
        config_raw = json.loads(config_bytes)
        config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}

        layer_digest = str(layers[0].get("digest", ""))
        layer_path = files[f"blobs/sha256/{digest_hex(layer_digest)}"]
        if sha256_digest(layer_path.read_bytes()) != layer_digest:
            raise ImageError(f"layer bytes do not match digest {layer_digest}")

        extracted = workdir / "bundle"
        safe_extract_bundle(layer_path, extracted, limits)
        staged = _stage_content_tree(extracted, store.root)
        try:
            revision = compute_revision_id(staged)
            declared = config.get("bundleRevision")
            if isinstance(declared, str) and declared and declared != revision:
                raise ImageError(
                    f"config declares revision {declared} but the layer content is {revision}"
                )
            # Permissive consumer: store the foreign manifest/config bytes
            # verbatim (unknown fields intact), never our re-serialization.
            store.put_blob(layer_path.read_bytes())
            store.put_blob(config_bytes)
            stored_digest = store.put_manifest(manifest_bytes)
            store.publish_tree(staged, layer_digest)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        title, description = (
            str(config.get("title", "")) or "Untitled bundle",
            str(config.get("description", "")),
        )
        return ImageRecord(
            manifest_digest=stored_digest,
            config_digest=str(config_descriptor.get("digest", "")),
            layer_digest=layer_digest,
            revision=revision,
            title=title,
            description=description,
            config=config,
        )
    except (ExtractError, StoreError, json.JSONDecodeError, KeyError) as exc:
        raise ImageError(f"invalid image tar: {exc}") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _read_layout_tar(tar_path: Path, workdir: Path, limits: ExtractLimits) -> dict[str, Path]:
    """Extract an OCI layout tar with the same discipline as bundle ingestion."""
    files: dict[str, Path] = {}
    total = 0
    try:
        tar = tarfile.open(tar_path, mode="r:")  # noqa: SIM115 — closed by the with below
    except tarfile.ReadError as exc:
        raise ImageError(f"not an OCI image tar: {tar_path.name}") from exc
    with tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isreg():
                raise ImageError(f"unsupported member in image tar: {member.name!r}")
            name = member.name.removeprefix("./")
            if name not in _LAYOUT_NAME_OK and not _is_blob_name(name):
                raise ImageError(f"unexpected member in image tar: {member.name!r}")
            if name in files:
                raise ImageError(f"duplicate member in image tar: {member.name!r}")
            total += member.size
            if total > limits.max_expanded_bytes:
                raise ImageError("image tar expands past the configured limit")
            source = tar.extractfile(member)
            if source is None:
                raise ImageError(f"unreadable member in image tar: {member.name!r}")
            target = workdir / "layout" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            files[name] = target
    for required in _LAYOUT_NAME_OK:
        if required not in files:
            raise ImageError(f"image tar is missing {required}")
    return files


def _is_blob_name(name: str) -> bool:
    parts = name.split("/")
    return (
        len(parts) == 3
        and parts[0] == "blobs"
        and parts[1] == "sha256"
        and len(parts[2]) == 64
        and all(c in "0123456789abcdef" for c in parts[2])
    )


def _load_json(files: dict[str, Path], name: str) -> dict[str, Any]:
    loaded = json.loads(files[name].read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ImageError(f"{name} is not a JSON object")
    return loaded


def _verified_blob(files: dict[str, Path], digest: str) -> bytes:
    key = f"blobs/sha256/{digest_hex(digest)}"
    if key not in files:
        raise ImageError(f"image tar is missing blob {digest}")
    data = files[key].read_bytes()
    if sha256_digest(data) != digest:
        raise ImageError(f"blob bytes do not match digest {digest}")
    return data

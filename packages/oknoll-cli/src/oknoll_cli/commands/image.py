"""`oknoll image` — OkNoll images as OCI artifacts in the local content store."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from okf_core import ExtractError
from oknoll_runtime import (
    Catalog,
    ImageError,
    StoreError,
    build_image_from_archive,
    build_image_from_tree,
    load_image,
    read_image_record,
    save_image,
)
from oknoll_runtime.locator import ALIAS_RE, TAG_RE

from oknoll_cli.commands.common import (
    STUB_CONTEXT,
    fail,
    not_yet,
    runtime_store_catalog,
    short_digest,
)

image_app = typer.Typer(
    help="Build, manage, and move OkNoll images (OCI artifacts containing OKF bundles).",
    no_args_is_help=True,
)


def _parse_name_tag(reference: str) -> tuple[str, str]:
    name, sep, tag = reference.partition(":")
    if not sep or not ALIAS_RE.match(name) or not TAG_RE.match(tag):
        raise fail(
            f"invalid image reference {reference!r} — expected <name>:<tag> "
            "(name matches [a-z0-9][a-z0-9._-]*)"
        )
    return name, tag


def _resolve_ref(catalog: Catalog, reference: str) -> str:
    """A manifest digest from a ``sha256:…`` spelling or a local ``name:tag``."""
    if reference.startswith("sha256:"):
        return reference
    name, tag = _parse_name_tag(reference)
    digest = catalog.resolve_tag(name, tag)
    if digest is None:
        raise fail(f"no local image tagged {reference!r} — see `oknoll image list`")
    return digest


@image_app.command("build")
def build(
    source: Path = typer.Argument(..., help="Bundle directory or OKF archive."),
    tag: str | None = typer.Option(
        None, "--tag", help="Local name:tag to point at the built image."
    ),
    title: str | None = typer.Option(None, "--title", help="Override the config title."),
    description: str | None = typer.Option(
        None, "--description", help="Override the config description."
    ),
) -> None:
    """Create a deterministic OCI artifact from a bundle tree or OKF archive."""
    store, catalog = runtime_store_catalog()
    try:
        if source.is_dir():
            record = build_image_from_tree(store, source, title=title, description=description)
        else:
            record = build_image_from_archive(store, source, title=title, description=description)
    except (ImageError, StoreError, ExtractError) as exc:
        raise fail(f"oknoll image build: {exc}") from exc

    typer.echo(f"built {record.manifest_digest}")
    typer.echo(f"  revision  {record.revision}")
    typer.echo(f"  title     {record.title}")
    if tag is not None:
        name, tag_part = _parse_name_tag(tag)
        catalog.set_tag(name, tag_part, record.manifest_digest)
        typer.echo(f"  tagged    {name}:{tag_part}")


@image_app.command("list")
def list_(
    json_output: bool = typer.Option(False, "--json", help="Emit the image list as JSON."),
) -> None:
    """List images in the local content store."""
    store, catalog = runtime_store_catalog()
    tags_by_digest: dict[str, list[str]] = {}
    for name, tag, digest in catalog.list_tags():
        tags_by_digest.setdefault(digest, []).append(f"{name}:{tag}")

    rows = []
    for digest in store.list_manifest_digests():
        record = read_image_record(store, digest)
        rows.append(
            {
                "digest": digest,
                "revision": record.revision,
                "title": record.title,
                "tags": tags_by_digest.get(digest, []),
            }
        )
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo("no images in the local store — `oknoll image build <bundle>`")
        return
    for row in rows:
        tags = ", ".join(row["tags"]) or "-"
        typer.echo(
            f"{short_digest(str(row['digest'])):22} {row['revision']:16} {tags:24} {row['title']}"
        )


@image_app.command("inspect")
def inspect(
    reference: str = typer.Argument(..., help="sha256:<digest> or local <name>:<tag>."),
) -> None:
    """Print an image's manifest and config (JSON)."""
    store, catalog = runtime_store_catalog()
    digest = _resolve_ref(catalog, reference)
    try:
        manifest = store.read_manifest(digest)
        record = read_image_record(store, digest)
    except (StoreError, ImageError) as exc:
        raise fail(f"oknoll image inspect: {exc}") from exc
    typer.echo(
        json.dumps({"digest": digest, "manifest": manifest, "config": record.config}, indent=2)
    )


@image_app.command("tag")
def tag(
    source: str = typer.Argument(..., help="sha256:<digest> or existing <name>:<tag>."),
    target: str = typer.Argument(..., help="New <name>:<tag> to add or move."),
) -> None:
    """Add or move a local tag on an image."""
    store, catalog = runtime_store_catalog()
    digest = _resolve_ref(catalog, source)
    if not store.has_manifest(digest):
        raise fail(f"no image {digest} in the local store")
    name, tag_part = _parse_name_tag(target)
    catalog.set_tag(name, tag_part, digest)
    typer.echo(f"{name}:{tag_part} → {digest}")


@image_app.command("remove")
def remove(
    reference: str = typer.Argument(..., help="sha256:<digest> or local <name>:<tag>."),
) -> None:
    """Remove an image (and its tags) from the local content store."""
    store, catalog = runtime_store_catalog()
    digest = _resolve_ref(catalog, reference)
    if digest in catalog.digests_in_use():
        aliases = [e.alias for e in catalog.list_entries() if e.oci_digest == digest]
        raise fail(
            f"image {short_digest(digest)} is installed as "
            f"{', '.join(repr(a) for a in aliases)} — `oknoll bundle uninstall` first"
        )
    try:
        store.remove_image(digest)
    except StoreError as exc:
        raise fail(f"oknoll image remove: {exc}") from exc
    catalog.remove_tags_for(digest)
    typer.echo(f"removed {digest}")


@image_app.command("save")
def save(
    reference: str = typer.Argument(..., help="sha256:<digest> or local <name>:<tag>."),
    output: Path = typer.Option(..., "--output", "-o", help="Output OCI image-layout tar."),
) -> None:
    """Export an image to an OCI image-layout tar for air-gapped transfer."""
    store, catalog = runtime_store_catalog()
    digest = _resolve_ref(catalog, reference)
    ref_name = None if reference.startswith("sha256:") else reference
    try:
        save_image(store, digest, output, reference=ref_name)
    except (StoreError, ImageError) as exc:
        raise fail(f"oknoll image save: {exc}") from exc
    typer.echo(f"saved {digest} → {output}")


@image_app.command("load")
def load(
    archive: Path = typer.Argument(..., help="OCI image-layout tar produced by `image save`."),
) -> None:
    """Import an image from an OCI image-layout tar, verifying every digest."""
    store, _ = runtime_store_catalog()
    try:
        record = load_image(store, archive)
    except (ImageError, StoreError, ExtractError) as exc:
        raise fail(f"oknoll image load: {exc}") from exc
    typer.echo(f"loaded {record.manifest_digest}")
    typer.echo(f"  revision  {record.revision}")
    typer.echo(f"  title     {record.title}")


@image_app.command("push", context_settings=STUB_CONTEXT)
def push() -> None:
    """Push an image to an OCI registry."""
    not_yet("image push", "the registry client")


@image_app.command("pull", context_settings=STUB_CONTEXT)
def pull() -> None:
    """Pull and verify an image into the local content store."""
    not_yet("image pull", "the registry client")

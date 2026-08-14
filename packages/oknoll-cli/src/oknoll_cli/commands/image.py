"""`oknoll image` — OkNoll images as OCI artifacts (arrives with the content store)."""

from __future__ import annotations

import typer

from oknoll_cli.commands.common import STUB_CONTEXT, not_yet

image_app = typer.Typer(
    help="Build, manage, and move OkNoll images (OCI artifacts containing OKF bundles).",
    no_args_is_help=True,
)


@image_app.command("build", context_settings=STUB_CONTEXT)
def build() -> None:
    """Create a deterministic OCI artifact from a bundle or OKF archive."""
    not_yet("image build", "the local content store")


@image_app.command("list", context_settings=STUB_CONTEXT)
def list_() -> None:
    """List images in the local content store."""
    not_yet("image list", "the local content store")


@image_app.command("inspect", context_settings=STUB_CONTEXT)
def inspect() -> None:
    """Inspect an image's manifest, config, and digests."""
    not_yet("image inspect", "the local content store")


@image_app.command("tag", context_settings=STUB_CONTEXT)
def tag() -> None:
    """Add or move a tag on a local image."""
    not_yet("image tag", "the local content store")


@image_app.command("remove", context_settings=STUB_CONTEXT)
def remove() -> None:
    """Remove an image from the local content store."""
    not_yet("image remove", "the local content store")


@image_app.command("save", context_settings=STUB_CONTEXT)
def save() -> None:
    """Export an image to an OCI-layout tar for air-gapped transfer."""
    not_yet("image save", "the local content store")


@image_app.command("load", context_settings=STUB_CONTEXT)
def load() -> None:
    """Import an image from an OCI-layout tar."""
    not_yet("image load", "the local content store")


@image_app.command("push", context_settings=STUB_CONTEXT)
def push() -> None:
    """Push an image to an OCI registry."""
    not_yet("image push", "the registry client")


@image_app.command("pull", context_settings=STUB_CONTEXT)
def pull() -> None:
    """Pull and verify an image into the local content store."""
    not_yet("image pull", "the registry client")

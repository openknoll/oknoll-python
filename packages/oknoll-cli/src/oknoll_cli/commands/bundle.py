"""`oknoll bundle` — operate on OKF bundles: validate, diff, pack, install."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import typer
from okf_core import (
    ExtractError,
    LintConfig,
    PipelineError,
    RevisionDiffError,
    check_reproducibility,
    diff_revisions,
    lint_bundle,
    pack_bundle,
    read_current_revision_id,
    revision_dir,
    safe_extract_bundle,
)
from oknoll_connectors import ConnectorError
from oknoll_providers import ProviderError
from oknoll_runtime import (
    CatalogError,
    ImageError,
    InstallError,
    StoreError,
    checkout_bundle,
    install_bundle,
)

from oknoll_cli.commands.common import (
    STUB_CONTEXT,
    fail,
    load_project,
    not_yet,
    parse_cli_locator,
    pipeline_sources,
    print_findings,
    print_health,
    provider_for,
    resolve_installed,
    runtime_store_catalog,
    short_digest,
)

bundle_app = typer.Typer(
    help="Validate, diff, pack, and (with the local content store) install OKF bundles.",
    no_args_is_help=True,
)


@bundle_app.command("lint")
def lint(
    path: Path = typer.Argument(
        Path("bundle"), help="Bundle directory to lint (any bundle, including others')."
    ),
    strict: bool = typer.Option(False, "--strict", help="Hygiene warnings also fail."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Run all five validation levels on a bundle."""
    report = lint_bundle(path, LintConfig(today=date.today().isoformat()))

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=False))
    else:
        print_findings(report)
        summary = report.to_dict()["summary"]
        typer.echo(
            f"{summary['errors']} error(s), {summary['warnings']} warning(s), "
            f"{summary['info']} info"
        )
        print_health(report.metrics)

    raise typer.Exit(code=0 if report.passed(strict=strict) else 1)


@bundle_app.command("pack")
def pack(
    profile: str = typer.Option(
        "okf", "--profile", help="okf (full) or plain (OKF-specific fields stripped)."
    ),
) -> None:
    """Produce a deterministic archive + manifest + checksums."""
    if profile not in ("okf", "plain"):
        typer.secho(
            f"oknoll bundle pack: unknown profile {profile!r} (okf|plain)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    config = load_project()
    current = read_current_revision_id(config.bundle_path)
    if current is None:
        raise fail("no published revision to pack — run `oknoll project build` first")

    # Canonical pack filename is .okf.tgz; .tar.gz stays accepted on input forever.
    suffix = "" if profile == "okf" else "-plain"
    archive_name = f"{config.name}-{current}{suffix}.okf.tgz"
    result = pack_bundle(
        revision_dir(config.bundle_path, current),
        config.root / "dist" / archive_name,
        profile=profile,
        member_root=f"{config.name}-{current}",
    )
    typer.echo(f"{result.archive_path.relative_to(config.root)}  ({result.file_count} files)")
    typer.echo(f"{result.checksum_path.relative_to(config.root)}  sha256={result.sha256}")


@bundle_app.command("diff")
def diff(
    rev_a: str | None = typer.Argument(None, metavar="[REV_A]", help="Base revision id."),
    rev_b: str | None = typer.Argument(
        None, metavar="[REV_B]", help="Target revision id (default: the current revision)."
    ),
    check: bool = typer.Option(
        False, "--check", help="Rebuild to temp and report reproducibility drift."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Semantic diff between two revisions, or reproducibility drift with --check."""
    if check:
        if rev_a is not None or rev_b is not None:
            raise fail("oknoll bundle diff: --check takes no revision arguments")
        config = load_project()
        provider = provider_for(config)
        sources = pipeline_sources(config)
        try:
            current, drift = check_reproducibility(
                bundle_dir=config.bundle_path,
                project_name=config.name,
                sources=sources,
                provider=provider,
                generation_version=config.generation_version,
            )
        except (PipelineError, ConnectorError, ProviderError) as exc:
            raise fail(f"oknoll bundle diff: {exc}") from exc

        if drift:
            for path, kind in drift:
                typer.secho(f"drift    {kind:24} {path}", fg=typer.colors.RED)
            raise fail(
                f"rebuild does not reproduce revision {current} ({len(drift)} file(s) drift)"
            )
        typer.secho(f"reproducible — rebuild matches revision {current}", fg=typer.colors.GREEN)
        return

    if rev_a is None:
        raise fail("oknoll bundle diff: give a revision id (optionally two), or --check")
    config = load_project()
    target = rev_b if rev_b is not None else read_current_revision_id(config.bundle_path)
    if target is None:
        raise fail("oknoll bundle diff: no published revision to compare against")
    try:
        report = diff_revisions(config.bundle_path, rev_a, target)
    except RevisionDiffError as exc:
        raise fail(f"oknoll bundle diff: {exc}") from exc
    if json_output:
        typer.echo(json.dumps(report, indent=2))
        return
    _render_revision_diff(report)


def _render_revision_diff(report: dict[str, object]) -> None:
    """Human-readable revision diff: role-classified counts, then the entries."""
    typer.echo(f"{report['rev_a']} → {report['rev_b']}")
    files: Any = report["files"]
    concepts: Any = report["concepts"]
    references: Any = report["references"]
    links: Any = report["links"]
    if not any((*files.values(), *links.values())):
        typer.echo("no content differences")
        return

    retitled = sum(1 for entry in concepts["changed"] if entry["retitled"])
    repinned = sum(1 for entry in references["changed"] if entry["repinned"])
    typer.echo(
        f"concepts    {len(concepts['added'])} added, {len(concepts['removed'])} removed, "
        f"{len(concepts['changed'])} changed ({retitled} retitled)"
    )
    for entry in concepts["added"]:
        typer.echo(f"  + {entry['path']} — {entry['title']}")
    for entry in concepts["removed"]:
        typer.echo(f"  - {entry['path']} — {entry['title']}")
    for entry in concepts["changed"]:
        note = (
            f" (retitled: {entry['title_a']!r} → {entry['title_b']!r})" if entry["retitled"] else ""
        )
        typer.echo(f"  ~ {entry['path']}{note}")

    typer.echo(
        f"references  {len(references['added'])} added, {len(references['removed'])} removed, "
        f"{len(references['changed'])} changed ({repinned} repinned)"
    )
    for entry in references["added"]:
        typer.echo(f"  + {entry['path']} — {entry['title']}")
    for entry in references["removed"]:
        typer.echo(f"  - {entry['path']} — {entry['title']}")
    for entry in references["changed"]:
        what = [
            label
            for label, flag in (
                ("uri", entry["uri_changed"]),
                ("source_hash", entry["source_hash_changed"]),
            )
            if flag
        ]
        note = f" (repinned: {', '.join(what)})" if what else ""
        typer.echo(f"  ~ {entry['path']}{note}")

    typer.echo(f"links       {len(links['added'])} added, {len(links['removed'])} removed")
    for src, dst in links["added"]:
        typer.echo(f"  + {src} → {dst}")
    for src, dst in links["removed"]:
        typer.echo(f"  - {src} → {dst}")

    typer.echo(
        f"files       {len(files['added'])} added, {len(files['removed'])} removed, "
        f"{len(files['changed'])} changed"
    )


@bundle_app.command("install")
def install(
    target: str = typer.Argument(
        ..., help="Bundle directory or OKF archive to install (remote locators arrive later)."
    ),
    name: str = typer.Option(..., "--name", help="Catalog alias to register."),
    update: bool = typer.Option(
        False, "--update", help="Atomically repoint the alias if it already exists."
    ),
) -> None:
    """Install an existing OKF bundle into the local content store and catalog."""
    locator = parse_cli_locator(target)
    if locator.kind in ("oci", "oknoll"):
        not_yet("bundle install", "the registry client")
    if locator.kind in ("bare", "local"):
        raise fail(f"{target!r} names a catalog alias, not an installable source")

    store, catalog = runtime_store_catalog()
    try:
        entry, _record, warnings = install_bundle(
            store,
            catalog,
            Path(locator.path).expanduser(),
            alias=name,
            update=update,
            today=date.today().isoformat(),
        )
    except (InstallError, CatalogError, ImageError, StoreError, ExtractError) as exc:
        raise fail(f"oknoll bundle install: {exc}") from exc

    typer.echo(f"installed {entry.alias}")
    typer.echo(f"  image     {entry.oci_digest}")
    typer.echo(f"  revision  {entry.okf_revision}")
    typer.echo(f"  title     {entry.title}")
    summary = json.loads(entry.lint_summary) if entry.lint_summary else {}
    if summary:
        typer.echo(
            f"  lint      {summary.get('errors', 0)} error(s), "
            f"{summary.get('warnings', 0)} warning(s)"
        )
    for warning in warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)


@bundle_app.command("uninstall")
def uninstall(
    target: str = typer.Argument(..., help="Catalog alias (or local:<alias>) to remove."),
) -> None:
    """Remove an installed bundle from the local catalog (the image stays in the store)."""
    _, catalog = runtime_store_catalog()
    entry = resolve_installed(catalog, target)
    try:
        catalog.remove(entry.alias)
    except CatalogError as exc:
        raise fail(f"oknoll bundle uninstall: {exc}") from exc
    typer.echo(
        f"uninstalled {entry.alias} — the image remains in the store "
        f"(`oknoll image remove {entry.oci_digest[:19]}…` reclaims it)"
    )


@bundle_app.command("list")
def list_(
    json_output: bool = typer.Option(False, "--json", help="Emit the catalog as JSON."),
) -> None:
    """List installed bundles in the local catalog."""
    _, catalog = runtime_store_catalog()
    entries = catalog.list_entries()
    if json_output:
        typer.echo(json.dumps([asdict(entry) for entry in entries], indent=2))
        return
    if not entries:
        typer.echo("no bundles installed — `oknoll bundle install <source> --name <alias>`")
        return
    for entry in entries:
        typer.echo(
            f"{entry.alias:20} {entry.okf_revision:16} "
            f"{short_digest(entry.oci_digest):22} {entry.title}"
        )


@bundle_app.command("inspect")
def inspect(
    target: str = typer.Argument(..., help="Catalog alias (or local:<alias>)."),
    json_output: bool = typer.Option(False, "--json", help="Emit the entry as JSON."),
) -> None:
    """Inspect an installed bundle: identity, provenance, lint summary."""
    _, catalog = runtime_store_catalog()
    entry = resolve_installed(catalog, target)
    if json_output:
        typer.echo(json.dumps(asdict(entry), indent=2))
        return
    typer.echo(f"alias        {entry.alias}")
    typer.echo(f"mode         {entry.mode}")
    typer.echo(f"reference    {entry.reference}")
    typer.echo(f"image        {entry.oci_digest}")
    typer.echo(f"revision     {entry.okf_revision}")
    typer.echo(f"title        {entry.title}")
    if entry.description:
        typer.echo(f"description  {entry.description}")
    typer.echo(f"signature    {entry.signature_state}")
    if entry.lint_summary:
        typer.echo(f"lint         {entry.lint_summary}")
    typer.echo(f"installed    {entry.installed_at}")
    if entry.last_used_at:
        typer.echo(f"last used    {entry.last_used_at}")


@bundle_app.command("checkout")
def checkout(
    target: str = typer.Argument(..., help="Catalog alias (or local:<alias>) to materialize."),
    dest: Path = typer.Argument(..., help="Destination directory (must be empty or absent)."),
) -> None:
    """Materialize a human-visible bundle tree pinned to the installed revision."""
    store, catalog = runtime_store_catalog()
    entry = resolve_installed(catalog, target)
    try:
        checkout_bundle(store, entry, dest)
    except (InstallError, StoreError) as exc:
        raise fail(f"oknoll bundle checkout: {exc}") from exc
    typer.echo(f"checked out {entry.alias} @ {entry.okf_revision} → {dest}")


@bundle_app.command("unpack")
def unpack(
    archive: Path = typer.Argument(..., help="OKF archive (.okf.tgz, .tar.gz, or .tar)."),
    dest: Path = typer.Argument(..., help="Destination directory (must not exist)."),
) -> None:
    """Safely extract an OKF archive into a directory (no store, no catalog)."""
    try:
        result = safe_extract_bundle(archive, dest)
    except ExtractError as exc:
        raise fail(f"oknoll bundle unpack: {exc}") from exc
    typer.echo(f"unpacked {result.file_count} file(s) → {result.dest}")


@bundle_app.command("connect", context_settings=STUB_CONTEXT)
def connect() -> None:
    """Register a hosted bundle in the local catalog without downloading it."""
    not_yet("bundle connect", "the registry client")

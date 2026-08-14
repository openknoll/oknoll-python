"""`oknoll bundle` — operate on OKF bundles: validate, diff, pack, install."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import typer
from okf_core import (
    LintConfig,
    PipelineError,
    RevisionDiffError,
    check_reproducibility,
    diff_revisions,
    lint_bundle,
    pack_bundle,
    read_current_revision_id,
    revision_dir,
)
from oknoll_connectors import ConnectorError
from oknoll_providers import ProviderError

from oknoll_cli.commands.common import (
    STUB_CONTEXT,
    fail,
    load_project,
    not_yet,
    pipeline_sources,
    print_findings,
    print_health,
    provider_for,
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

    suffix = "" if profile == "okf" else "-plain"
    archive_name = f"{config.name}-{current}{suffix}.tar.gz"
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


@bundle_app.command("install", context_settings=STUB_CONTEXT)
def install() -> None:
    """Install an existing OKF bundle (directory, archive, or locator) into the local store."""
    not_yet("bundle install", "the local content store")


@bundle_app.command("uninstall", context_settings=STUB_CONTEXT)
def uninstall() -> None:
    """Remove an installed bundle from the local catalog."""
    not_yet("bundle uninstall", "the local content store")


@bundle_app.command("list", context_settings=STUB_CONTEXT)
def list_() -> None:
    """List installed bundles in the local catalog."""
    not_yet("bundle list", "the local content store")


@bundle_app.command("inspect", context_settings=STUB_CONTEXT)
def inspect() -> None:
    """Inspect an installed bundle (identity, provenance, contents)."""
    not_yet("bundle inspect", "the local content store")


@bundle_app.command("checkout", context_settings=STUB_CONTEXT)
def checkout() -> None:
    """Materialize a human-visible bundle tree from the local store."""
    not_yet("bundle checkout", "the local content store")


@bundle_app.command("unpack", context_settings=STUB_CONTEXT)
def unpack() -> None:
    """Safely extract an OKF archive into a directory."""
    not_yet("bundle unpack", "the local content store")


@bundle_app.command("connect", context_settings=STUB_CONTEXT)
def connect() -> None:
    """Register a hosted bundle in the local catalog without downloading it."""
    not_yet("bundle connect", "the registry client")

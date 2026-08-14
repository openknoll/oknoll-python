"""`oknoll project` — author-side project lifecycle."""

from __future__ import annotations

from pathlib import Path

import typer
from okf_core import PipelineError, build_revision
from oknoll_connectors import ConnectorError
from oknoll_providers import ProviderError

from oknoll_cli import project
from oknoll_cli.commands.common import (
    fail,
    load_project,
    pipeline_sources,
    print_findings,
    provider_for,
)

project_app = typer.Typer(
    help="Create and build oknoll projects (sources → published bundle revisions).",
    no_args_is_help=True,
)


@project_app.command("init")
def init(
    path: Path = typer.Argument(Path("."), help="Project directory (created if missing)."),
    name: str | None = typer.Option(
        None, "--name", help="Project name (defaults to the directory name)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing oknoll.toml."),
) -> None:
    """Create project config, source/output dirs, ignore file, and a bundle skeleton."""
    try:
        created = project.init_project(path, name=name, force=force)
    except project.ProjectExistsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    for rel in created:
        typer.echo(f"created {rel}")
    typer.secho(f"Initialized oknoll project in {path}", fg=typer.colors.GREEN)


@project_app.command("build")
def build() -> None:
    """Acquire → … → publish a new immutable local revision."""
    config = load_project()
    provider = provider_for(config)
    sources = pipeline_sources(config)

    try:
        outcome = build_revision(
            bundle_dir=config.bundle_path,
            project_name=config.name,
            sources=sources,
            provider=provider,
            generation_version=config.generation_version,
        )
    except (PipelineError, ConnectorError, ProviderError) as exc:
        raise fail(f"oknoll project build: {exc}") from exc

    print_findings(outcome.lint_report)
    counts = outcome.report["counts"]
    cache = outcome.report["cache"]
    typer.echo(
        f"{counts['concepts']} concept(s) from {counts['source_files']} source file(s); "
        f"cache {cache['hits']} hit(s), {cache['misses']} miss(es)"
    )
    if not outcome.lint_report.passed():
        raise fail(f"build failed lint — staged output kept at {outcome.staged_dir} for inspection")
    if outcome.published:
        typer.secho(f"published revision {outcome.revision_id}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"no changes — current revision remains {outcome.revision_id}")

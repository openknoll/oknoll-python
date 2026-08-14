"""`oknoll source` — register project sources with connector auto-probe."""

from __future__ import annotations

from pathlib import Path

import typer
from okf_core import SourceRef
from oknoll_connectors import FilesConnector, GitHubConnector, WebConnector, parse_github_source
from oknoll_connectors.fetch import canonicalize_url

from oknoll_cli import project
from oknoll_cli.commands.common import fail, load_project

_GIT_PREFIXES = ("git@", "ssh://")

source_app = typer.Typer(
    help="Register files, websites, and GitHub repositories as project sources.",
    no_args_is_help=True,
)


@source_app.command("add")
def add(
    source: str = typer.Argument(
        ..., help="File, directory, URL, repository, or transcript source."
    ),
) -> None:
    """Register a source with connector auto-probe."""
    config = load_project()

    if parse_github_source(source) is not None:
        _add_github(config, source)
        return
    if source.startswith(_GIT_PREFIXES) or source.endswith(".git"):
        raise fail(
            f"oknoll source add: {source!r} looks like a git source, but only GitHub "
            "repositories are supported (https://github.com/owner/repo or "
            "git@github.com:owner/repo)"
        )
    if source.lower().startswith(("http://", "https://")):
        _add_web(config, source)
        return

    path = Path(source)
    resolved = path if path.is_absolute() else (Path.cwd() / path)
    try:
        uri = resolved.resolve().relative_to(config.root).as_posix()
    except ValueError:
        uri = resolved.resolve().as_posix()  # outside the project tree; keep absolute

    connector = FilesConnector(
        base_dir=config.root, ignore_patterns=project.ignore_patterns(config.root)
    )
    probe = connector.probe(SourceRef(connector="files", uri=uri))
    if not probe.supported:
        raise fail(f"oknoll source add: files connector cannot use {source!r}: {probe.detail}")

    if project.register_source(config.root, "files", uri):
        typer.secho(
            f"registered files source: {uri} ({probe.kind}: {probe.detail})", fg=typer.colors.GREEN
        )
    else:
        typer.echo(f"source already registered: {uri}")


def _add_web(config: project.ProjectConfig, source: str) -> None:
    uri = canonicalize_url(source)
    probe = WebConnector().probe(SourceRef(connector="web", uri=uri))
    if not probe.supported:
        raise fail(f"oknoll source add: web connector cannot use {source!r}: {probe.detail}")
    if project.register_source(config.root, "web", uri):
        typer.secho(
            f"registered web source: {uri} ({probe.kind}: {probe.detail})", fg=typer.colors.GREEN
        )
    else:
        typer.echo(f"source already registered: {uri}")


def _add_github(config: project.ProjectConfig, source: str) -> None:
    uri = source.strip()
    probe = GitHubConnector().probe(SourceRef(connector="github", uri=uri))
    if not probe.supported:
        raise fail(f"oknoll source add: github connector cannot use {source!r}: {probe.detail}")
    if project.register_source(config.root, "github", uri):
        typer.secho(
            f"registered github source: {uri} ({probe.kind}: {probe.detail})",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(f"source already registered: {uri}")

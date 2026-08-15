"""Shared plumbing for the command namespaces.

CLI-internal helpers used by more than one namespace module: project loading
with env layering, pipeline source construction, provider resolution, and
output formatting. Nothing here is part of the public command surface.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer
from okf_core import (
    EmbeddingProvider,
    LintReport,
    ModelProvider,
    PipelineSource,
    Severity,
    SourceRef,
)
from oknoll_connectors import FetchPolicy, FilesConnector, GitHubConnector, WebConnector
from oknoll_providers import ProviderError, load_env
from oknoll_providers import resolve as resolve_provider_spec
from oknoll_providers import resolve_embedder as resolve_embedder_spec
from oknoll_runtime import Catalog, CatalogEntry, LocatorError, Store, parse_locator, runtime_dirs

from oknoll_cli import global_config, project

NOT_YET_EXIT = 2

# Commands whose capability has not shipped yet swallow any arguments so they
# always reach the "not yet" message instead of a usage error.
STUB_CONTEXT: dict[str, Any] = {"allow_extra_args": True, "ignore_unknown_options": True}


def not_yet(command: str, capability: str) -> None:
    typer.secho(
        f"oknoll {command}: not implemented yet — planned for {capability}.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=NOT_YET_EXIT)


def fail(message: str, code: int = 1) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=code)


def load_project() -> project.ProjectConfig:
    root = project.find_project_root(Path.cwd())
    if root is None:
        raise fail(
            "no oknoll.toml found in this directory or any parent — run `oknoll project init`"
        )
    try:
        config = project.load_project(root)
    except project.ProjectError as exc:
        raise fail(str(exc)) from exc
    # Provider credentials (ANTHROPIC_API_KEY, OLLAMA_HOST, GITHUB_TOKEN, ...)
    # layer without ever overriding the real environment: shell env wins, then
    # the project-local .env, then ~/.oknoll/.env + config.toml.
    try:
        load_env(root)
        global_config.apply_global_env()
    except (ProviderError, global_config.GlobalConfigError) as exc:
        raise fail(str(exc)) from exc
    return config


def pipeline_sources(config: project.ProjectConfig) -> list[PipelineSource]:
    if not config.sources:
        raise fail("no sources registered — run `oknoll source add SOURCE` first")
    patterns = project.ignore_patterns(config.root)
    policy = FetchPolicy()
    sources: list[PipelineSource] = []
    for registered in config.sources:
        connector: FilesConnector | WebConnector | GitHubConnector
        if registered.connector == "files":
            connector = FilesConnector(base_dir=config.root, ignore_patterns=patterns)
        elif registered.connector == "web":
            connector = WebConnector()
        elif registered.connector == "github":
            connector = GitHubConnector(token=os.environ.get("GITHUB_TOKEN"))
        else:
            raise fail(
                f"unknown connector {registered.connector!r} in oknoll.toml "
                "(available: files, web, github)"
            )
        sources.append(
            PipelineSource(
                source=SourceRef(connector=registered.connector, uri=registered.uri),
                connector=connector,
                policy=policy,
            )
        )
    return sources


def runtime_store_catalog() -> tuple[Store, Catalog]:
    """The machine-level content store and catalog (data dir per platformdirs)."""
    dirs = runtime_dirs()
    return Store(dirs.data), Catalog(dirs.data)


def parse_cli_locator(text: str) -> Any:
    """Parse a locator typed by the user; parse errors are user errors (exit 2)."""
    try:
        return parse_locator(text)
    except LocatorError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def resolve_installed(catalog: Catalog, target: str) -> CatalogEntry:
    """Resolve a bare name or ``local:`` locator to an installed catalog entry."""
    locator = parse_cli_locator(target)
    if locator.kind not in ("bare", "local"):
        raise fail(f"{target!r} does not name an installed bundle (expected local:<alias>)")
    alias = locator.name or ""
    entry = catalog.get(alias)
    if entry is None:
        raise fail(f"no installed bundle named {alias!r} — see `oknoll bundle list`")
    return entry


def short_digest(digest: str) -> str:
    return digest[:19] + "…" if len(digest) > 20 else digest


def display_path(path: Path, root: Path) -> str:
    """Project-relative when it is inside the project, absolute otherwise."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def global_defaults() -> global_config.GlobalConfig:
    try:
        return global_config.load_global_config()
    except global_config.GlobalConfigError as exc:
        raise fail(str(exc)) from exc


def provider_for(config: project.ProjectConfig, override: str | None = None) -> ModelProvider:
    spec = override or config.model or global_defaults().model or "stub"
    try:
        return resolve_provider_spec(spec)
    except (ValueError, ProviderError) as exc:
        raise fail(str(exc)) from exc


def embedder_for(config: project.ProjectConfig, override: str | None = None) -> EmbeddingProvider:
    spec = override or config.embedder or global_defaults().embedder or "stub"
    try:
        return resolve_embedder_spec(spec)
    except (ValueError, ProviderError) as exc:
        raise fail(str(exc)) from exc


def provider_without_project(override: str | None = None) -> ModelProvider:
    """Provider resolution for locator-based asks — no project config involved."""
    try:
        load_env(Path.cwd())
        global_config.apply_global_env()
    except (ProviderError, global_config.GlobalConfigError) as exc:
        raise fail(str(exc)) from exc
    spec = override or global_defaults().model or "stub"
    try:
        return resolve_provider_spec(spec)
    except (ValueError, ProviderError) as exc:
        raise fail(str(exc)) from exc


def embedder_without_project(override: str | None = None) -> EmbeddingProvider:
    spec = override or global_defaults().embedder or "stub"
    try:
        return resolve_embedder_spec(spec)
    except (ValueError, ProviderError) as exc:
        raise fail(str(exc)) from exc


def print_findings(report: LintReport) -> None:
    for finding in report.sorted_findings():
        color = {
            Severity.ERROR: typer.colors.RED,
            Severity.WARNING: typer.colors.YELLOW,
            Severity.INFO: typer.colors.BLUE,
        }[finding.severity]
        location = finding.path or "(bundle)"
        typer.secho(
            f"{finding.severity.value:7} {finding.code:40} {location}: {finding.message}",
            fg=color,
        )


def print_health(metrics: dict[str, Any]) -> None:
    if not metrics:
        return
    coverage = metrics["source_coverage"]
    typer.echo(
        f"health: {coverage['sourced']}/{coverage['total']} concepts sourced, "
        f"{metrics['orphan_concepts']['count']} orphan(s), "
        f"{metrics['broken_links']['count']} broken link(s), "
        f"{metrics['freshness']['stale']} stale, "
        f"{metrics['uncited_references']['count']} uncited reference(s)"
    )

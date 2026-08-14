"""`oknoll config` — machine-level configuration (unchanged from v0.3)."""

from __future__ import annotations

from pathlib import Path

import typer
from oknoll_providers import ProviderError

from oknoll_cli import global_config, project
from oknoll_cli.commands.common import fail, load_project

config_app = typer.Typer(
    help="Inspect and set machine-level configuration (~/.oknoll).", no_args_is_help=True
)

_SETTING_KEYS_HELP = "One of: " + ", ".join(global_config.SETTINGS)


def _bundle_settings() -> tuple[str | None, str | None]:
    """Bundle-declared model/embedder when inside a project; env layers applied either way."""
    if project.find_project_root(Path.cwd()) is None:
        try:
            global_config.apply_global_env()
        except (ProviderError, global_config.GlobalConfigError) as exc:
            raise fail(str(exc)) from exc
        return None, None
    config = load_project()
    return config.model, config.embedder


def _effective_settings() -> list[global_config.EffectiveSetting]:
    bundle_model, bundle_embedder = _bundle_settings()
    try:
        return global_config.effective_settings(bundle_model, bundle_embedder)
    except global_config.GlobalConfigError as exc:
        raise fail(str(exc)) from exc


@config_app.command("list")
def config_list() -> None:
    """Show the effective settings and where each one comes from."""
    for row in _effective_settings():
        typer.echo(f"{row.key:24} {row.value:36} {row.source}")


@config_app.command("get")
def config_get(key: str = typer.Argument(..., help=_SETTING_KEYS_HELP)) -> None:
    """Print one effective value (script-friendly)."""
    if key not in global_config.SETTINGS:
        raise fail(f"unknown setting {key!r} — available: {', '.join(global_config.SETTINGS)}")
    typer.echo(next(row.value for row in _effective_settings() if row.key == key))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help=_SETTING_KEYS_HELP),
    value: str = typer.Argument(..., help="The value to store."),
) -> None:
    """Write one machine-level default to ~/.oknoll/config.toml (a bundle's toml still wins)."""
    try:
        path = global_config.set_setting(key, value)
    except global_config.GlobalConfigError as exc:
        raise fail(str(exc)) from exc
    typer.secho(f"{key} = {value.strip()}  ({global_config.display(path)})", fg=typer.colors.GREEN)


@config_app.command("unset")
def config_unset(key: str = typer.Argument(..., help=_SETTING_KEYS_HELP)) -> None:
    """Remove one machine-level default from ~/.oknoll/config.toml."""
    try:
        removed = global_config.unset_setting(key)
    except global_config.GlobalConfigError as exc:
        raise fail(str(exc)) from exc
    if removed:
        typer.secho(f"{key} removed", fg=typer.colors.GREEN)
    else:
        typer.secho(f"{key} was not set", fg=typer.colors.YELLOW)

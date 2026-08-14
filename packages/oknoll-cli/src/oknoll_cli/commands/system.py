"""`oknoll system` — machine-level surface: plugins, doctor, eval, viz."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import typer
from okf_core import ExplorerError, write_viz
from oknoll_providers import ProviderError, load_env, ping_ollama
from oknoll_providers import resolve as resolve_provider_spec
from oknoll_providers import resolve_embedder as resolve_embedder_spec

from oknoll_cli import global_config, plugins, project
from oknoll_cli.commands.common import fail, load_project

system_app = typer.Typer(
    help="Machine-level tools: connector plugins, setup doctor, eval, and viz.",
    no_args_is_help=True,
)
plugins_app = typer.Typer(
    help="List, inspect, and validate installed connectors.", no_args_is_help=True
)
system_app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list() -> None:
    """List installed connector plugins."""
    for name in plugins.INSTALLED:
        info = plugins.describe(plugins.get(name))
        typer.echo(f"{info['id']:8} {info['version']:8} {info['summary']}")


@plugins_app.command("inspect")
def plugins_inspect(name: str = typer.Argument(...)) -> None:
    """Inspect one installed connector plugin."""
    try:
        info = plugins.describe(plugins.get(name))
    except plugins.PluginError as exc:
        raise fail(f"oknoll system plugins inspect: {exc}") from exc
    for key in ("id", "version", "module", "summary"):
        typer.echo(f"{key:12} {info[key]}")
    typer.echo(f"{'capabilities':12} {', '.join(info['capabilities']) or '(none declared)'}")


@plugins_app.command("validate")
def plugins_validate(name: str = typer.Argument(...)) -> None:
    """Validate one installed connector plugin against the connector protocol."""
    try:
        problems = plugins.validate(plugins.get(name))
    except plugins.PluginError as exc:
        raise fail(f"oknoll system plugins validate: {exc}") from exc
    if problems:
        for problem in problems:
            typer.secho(f"problem  {problem}", fg=typer.colors.RED)
        raise fail(f"{name}: {len(problems)} protocol problem(s)")
    typer.secho(
        f"{name}: conforms to the connector protocol (behavioral contract is "
        "covered by its ConnectorContractSuite tests)",
        fg=typer.colors.GREEN,
    )


@system_app.command("viz")
def viz(
    bundle: Path | None = typer.Option(
        None, "--bundle", help="Bundle directory (default: the project's bundle)."
    ),
    out: Path = typer.Option(Path("viz.html"), "--out", help="Output HTML path."),
) -> None:
    """Render the bundle link graph to one self-contained offline HTML file."""
    if bundle is None:
        config = load_project()
        bundle = config.bundle_path
    if not bundle.is_dir():
        raise fail(f"oknoll system viz: bundle directory not found: {bundle}")
    stats = write_viz(bundle, out, today=date.today().isoformat())
    typer.echo(f"{out}  ({stats['nodes']} nodes, {stats['edges']} edges, {stats['bytes']} bytes)")


@system_app.command("eval")
def eval_cmd(
    benchmark_file: Path = typer.Argument(
        ..., help="Benchmark spec (TOML): frozen bundles + questions with gold evidence."
    ),
    model: str = typer.Option(
        "stub", "--model", help="Answer model for both conditions (stub|anthropic[:m]|ollama:m)."
    ),
    embedder: str = typer.Option(
        "stub", "--embedder", help="Embedder for the rag condition (stub|ollama:<model>)."
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Output directory (default: next to the benchmark file)."
    ),
) -> None:
    """Run the PD-vs-RAG descriptive benchmark and write the comparison report."""
    from oknoll_eval import BenchmarkError, load_benchmark, render_report, run_benchmark

    # eval takes secrets/endpoints from the env chain but keeps its explicit
    # "stub" option defaults — benchmark runs never inherit settings from
    # config files, so a machine default can't silently change a comparison.
    try:
        load_env(Path.cwd())
        global_config.apply_global_env()
    except (ProviderError, global_config.GlobalConfigError) as exc:
        raise fail(f"oknoll system eval: {exc}") from exc
    try:
        benchmark = load_benchmark(benchmark_file)
    except BenchmarkError as exc:
        raise fail(f"oknoll system eval: {exc}") from exc

    try:
        answer_provider = resolve_provider_spec(model)
        rag_embedder = resolve_embedder_spec(embedder)
    except (ValueError, ProviderError) as exc:
        raise fail(f"oknoll system eval: {exc}") from exc

    try:
        results = run_benchmark(
            benchmark,
            provider=answer_provider,
            embedder=rag_embedder,
            today=date.today().isoformat(),
        )
    except (ExplorerError, ValueError, ProviderError) as exc:
        raise fail(f"oknoll system eval: {exc}") from exc

    out_dir = out if out is not None else benchmark_file.resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"eval-{benchmark.name}-results.json"
    report_path = out_dir / f"eval-{benchmark.name}-report.md"
    results_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = render_report(results)
    report_path.write_text(report, encoding="utf-8")

    typer.echo(report)
    typer.secho(f"results: {results_path}", fg=typer.colors.GREEN)
    typer.secho(f"report:  {report_path}", fg=typer.colors.GREEN)


@system_app.command("doctor")
def doctor() -> None:
    """Check the machine setup: ~/.oknoll, secrets, providers, project wiring."""
    problems = 0

    def ok(message: str) -> None:
        typer.echo(f"  ok    {message}")

    def warn(message: str) -> None:
        typer.secho(f"  warn  {message}", fg=typer.colors.YELLOW)

    def failed(message: str) -> None:
        nonlocal problems
        problems += 1
        typer.secho(f"  FAIL  {message}", fg=typer.colors.RED)

    home = global_config.oknoll_home()
    note = "" if home.is_dir() else " (not created yet — `oknoll config set` creates it)"
    ok(f"oknoll home: {global_config.display(home)}{note}")

    config: global_config.GlobalConfig | None
    try:
        config = global_config.load_global_config()
    except global_config.GlobalConfigError as exc:
        config = None
        failed(str(exc))

    root = project.find_project_root(Path.cwd())
    bundle_model: str | None = None
    bundle_embedder: str | None = None
    if root is None:
        ok("project: none (not inside a bundle — machine-level checks only)")
    else:
        try:
            project_config = project.load_project(root)
            bundle_model = project_config.model
            bundle_embedder = project_config.embedder
            ok(f"project: {project_config.name} ({global_config.display(root)})")
        except project.ProjectError as exc:
            failed(str(exc))

    # Env layering in the same order every command uses: project .env first,
    # then ~/.oknoll/.env + config.toml defaults.
    try:
        if root is not None:
            load_env(root)
        if config is not None:
            global_config.apply_global_env()
    except (ProviderError, global_config.GlobalConfigError) as exc:
        failed(str(exc))

    env_paths = ([root / ".env"] if root is not None else []) + [home / ".env"]
    for env_path in env_paths:
        if not env_path.is_file():
            continue
        mode = env_path.stat().st_mode & 0o777
        if mode & 0o077:
            warn(
                f"{global_config.display(env_path)} is readable by other users "
                f"(mode {mode:o}) — run `chmod 600 {env_path}`"
            )
        else:
            ok(f"{global_config.display(env_path)}: present (mode {mode:o})")

    if config is not None:
        rows = {
            row.key: row for row in global_config.effective_settings(bundle_model, bundle_embedder)
        }
        for row in rows.values():
            error = global_config.SETTINGS[row.key][1](row.value)
            if error is not None:
                failed(f"{row.key} = {row.value!r} ({row.source}): {error}")
            else:
                ok(f"{row.key} = {row.value}  ({row.source})")

        model_value = rows["build.model"].value
        if os.environ.get("ANTHROPIC_API_KEY"):
            ok("ANTHROPIC_API_KEY: set")
        elif model_value.partition(":")[0] == "anthropic":
            failed(
                f"ANTHROPIC_API_KEY: not set — required by model {model_value!r}; "
                f"add it to the project .env or {global_config.display(home / '.env')}"
            )
        else:
            ok("ANTHROPIC_API_KEY: not set (only needed for anthropic models)")
        github = "set" if os.environ.get("GITHUB_TOKEN") else "not set (optional — GitHub sources)"
        ok(f"GITHUB_TOKEN: {github}")

        host_row = rows["providers.ollama.host"]
        needs_ollama = model_value.startswith("ollama:") or rows["rag.embedder"].value.startswith(
            "ollama:"
        )
        if needs_ollama or host_row.source != "default":
            try:
                version = ping_ollama(host_row.value)
                ok(f"ollama: reachable at {host_row.value} (server version {version})")
            except ProviderError as exc:
                if needs_ollama:
                    failed(str(exc))
                else:
                    warn(str(exc))
        else:
            ok("ollama: not configured (stub providers everywhere)")

    if problems:
        raise fail(f"doctor: {problems} problem(s) found")
    typer.secho("doctor: all checks passed", fg=typer.colors.GREEN)

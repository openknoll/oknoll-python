"""oknoll CLI — the frozen §5.1 command surface (design D15, ADR references in docs/).

Functional after Phase 2: `init`, `add`, `build`, `lint`, `pack`, `diff --check`.
Every other command is present so the grammar is frozen from day one, and fails
with a clear "not yet" error naming the phase that delivers it. Adding or
renaming a command requires a decision-record update — no aliases from
superseded designs (sync, validate, explore, export).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import typer
from okf_core import (
    EmbeddingProvider,
    ExplorerError,
    LintReport,
    ModelProvider,
    PipelineError,
    PipelineSource,
    Severity,
    SourceRef,
    answer_question,
    build_revision,
    check_reproducibility,
    lint_bundle,
    pack_bundle,
    read_current_revision_id,
    revision_dir,
    write_trace,
)
from okf_core import __version__ as core_version
from oknoll_connectors import (
    ConnectorError,
    FetchPolicy,
    FilesConnector,
    GitHubConnector,
    WebConnector,
    parse_github_source,
)
from oknoll_connectors.fetch import canonicalize_url
from oknoll_providers import ProviderError, load_env
from oknoll_providers import resolve as resolve_provider_spec
from oknoll_providers import resolve_embedder as resolve_embedder_spec

from oknoll_cli import conversations, plugins, project

NOT_YET_EXIT = 2
_GIT_PREFIXES = ("git@", "ssh://")

app = typer.Typer(
    name="oknoll",
    help="Local-first OKF bundle compiler and deterministic explorer.",
    no_args_is_help=True,
    add_completion=False,
)
plugin_app = typer.Typer(
    help="List, inspect, and validate installed connectors.", no_args_is_help=True
)
keys_app = typer.Typer(help="Manage API keys for headless automation.", no_args_is_help=True)
app.add_typer(plugin_app, name="plugin")
app.add_typer(keys_app, name="keys")


def _not_yet(command: str, phase: str) -> None:
    typer.secho(
        f"oknoll {command}: not implemented yet — planned for {phase} "
        "(docs/implementation-plan.md)",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=NOT_YET_EXIT)


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=code)


def _load_project() -> project.ProjectConfig:
    root = project.find_project_root(Path.cwd())
    if root is None:
        raise _fail("no oknoll.toml found in this directory or any parent — run `oknoll init`")
    try:
        config = project.load_project(root)
    except project.ProjectError as exc:
        raise _fail(str(exc)) from exc
    # Project-local .env supplies provider credentials (ANTHROPIC_API_KEY,
    # OLLAMA_HOST, GITHUB_TOKEN, ...) without ever overriding the real environment.
    try:
        load_env(root)
    except ProviderError as exc:
        raise _fail(str(exc)) from exc
    return config


def _pipeline_sources(config: project.ProjectConfig) -> list[PipelineSource]:
    if not config.sources:
        raise _fail("no sources registered — run `oknoll add SOURCE` first")
    patterns = project.ignore_patterns(config.root)
    policy = FetchPolicy()
    pipeline_sources: list[PipelineSource] = []
    for registered in config.sources:
        connector: FilesConnector | WebConnector | GitHubConnector
        if registered.connector == "files":
            connector = FilesConnector(base_dir=config.root, ignore_patterns=patterns)
        elif registered.connector == "web":
            connector = WebConnector()
        elif registered.connector == "github":
            connector = GitHubConnector(token=os.environ.get("GITHUB_TOKEN"))
        else:
            raise _fail(
                f"unknown connector {registered.connector!r} in oknoll.toml "
                "(available: files, web, github)"
            )
        pipeline_sources.append(
            PipelineSource(
                source=SourceRef(connector=registered.connector, uri=registered.uri),
                connector=connector,
                policy=policy,
            )
        )
    return pipeline_sources


def _display_path(path: Path, root: Path) -> str:
    """Project-relative when it is inside the project, absolute otherwise."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _provider_for(config: project.ProjectConfig, override: str | None = None) -> ModelProvider:
    try:
        return resolve_provider_spec(override or config.model)
    except (ValueError, ProviderError) as exc:
        raise _fail(str(exc)) from exc


def _embedder_for(config: project.ProjectConfig, override: str | None = None) -> EmbeddingProvider:
    try:
        return resolve_embedder_spec(override or config.embedder)
    except (ValueError, ProviderError) as exc:
        raise _fail(str(exc)) from exc


def _print_findings(report: LintReport) -> None:
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


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"oknoll {core_version}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """oknoll — build, lint, explore, and pack portable OKF v0.2 bundles."""


@app.command()
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


@app.command()
def lint(
    path: Path = typer.Argument(
        Path("bundle"), help="Bundle directory to lint (any bundle, including others')."
    ),
    strict: bool = typer.Option(False, "--strict", help="Hygiene warnings also fail."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Run all five validation levels on a bundle (design §4.5)."""
    report = lint_bundle(path)

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=False))
    else:
        _print_findings(report)
        summary = report.to_dict()["summary"]
        typer.echo(
            f"{summary['errors']} error(s), {summary['warnings']} warning(s), "
            f"{summary['info']} info"
        )

    raise typer.Exit(code=0 if report.passed(strict=strict) else 1)


@app.command()
def add(
    source: str = typer.Argument(
        ..., help="File, directory, URL, repository, or transcript source."
    ),
) -> None:
    """Register a source with connector auto-probe."""
    if source.startswith("cloud:"):
        _not_yet("add (cloud sources)", "Phase 8 (hosted control plane)")

    config = _load_project()

    if parse_github_source(source) is not None:
        _add_github(config, source)
        return
    if source.startswith(_GIT_PREFIXES) or source.endswith(".git"):
        raise _fail(
            f"oknoll add: {source!r} looks like a git source, but only GitHub "
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
        raise _fail(f"oknoll add: files connector cannot use {source!r}: {probe.detail}")

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
        raise _fail(f"oknoll add: web connector cannot use {source!r}: {probe.detail}")
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
        raise _fail(f"oknoll add: github connector cannot use {source!r}: {probe.detail}")
    if project.register_source(config.root, "github", uri):
        typer.secho(
            f"registered github source: {uri} ({probe.kind}: {probe.detail})",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(f"source already registered: {uri}")


@app.command()
def build() -> None:
    """Acquire → … → publish a new immutable local revision."""
    config = _load_project()
    provider = _provider_for(config)
    sources = _pipeline_sources(config)

    try:
        outcome = build_revision(
            bundle_dir=config.bundle_path,
            project_name=config.name,
            sources=sources,
            provider=provider,
        )
    except (PipelineError, ConnectorError, ProviderError) as exc:
        raise _fail(f"oknoll build: {exc}") from exc

    _print_findings(outcome.lint_report)
    counts = outcome.report["counts"]
    cache = outcome.report["cache"]
    typer.echo(
        f"{counts['concepts']} concept(s) from {counts['source_files']} source file(s); "
        f"cache {cache['hits']} hit(s), {cache['misses']} miss(es)"
    )
    if not outcome.lint_report.passed():
        raise _fail(
            f"build failed lint — staged output kept at {outcome.staged_dir} for inspection"
        )
    if outcome.published:
        typer.secho(f"published revision {outcome.revision_id}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"no changes — current revision remains {outcome.revision_id}")


@app.command()
def ask(
    question: str = typer.Argument(
        ..., help="One-shot question answered with citations and a trace."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit answer, citations, warnings, and trace as JSON."
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=(
            "Override the configured build.model for this question, e.g. "
            "anthropic:claude-opus-5 or ollama:llama3 (ADR-0004)."
        ),
    ),
    mode: str = typer.Option(
        "pd",
        "--mode",
        help="Retrieval condition: pd (progressive disclosure) or rag (vector baseline).",
    ),
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help=(
            "Override the configured rag.embedder for --mode rag, e.g. "
            "ollama:nomic-embed-text (stub is the deterministic default)."
        ),
    ),
) -> None:
    """One-shot answer over the current bundle revision (design §5.2, §7, §9.2)."""
    if mode not in ("pd", "rag"):
        typer.secho(f"oknoll ask: unknown mode {mode!r} (pd|rag)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    config = _load_project()
    provider = _provider_for(config, override=model)

    if read_current_revision_id(config.bundle_path) is None:
        raise _fail("no published revision to explore — run `oknoll build` first")

    try:
        result = answer_question(
            bundle_dir=config.bundle_path,
            question=question,
            provider=provider,
            today=date.today().isoformat(),
            condition=mode,
            embedder=_embedder_for(config, override=embedder) if mode == "rag" else None,
        )
    except (ExplorerError, ValueError, ProviderError) as exc:
        raise _fail(f"oknoll ask: {exc}") from exc

    trace_path = write_trace(config.bundle_path, result)
    # An absolute [paths].bundle puts the trace outside the project root.
    shown_trace = _display_path(trace_path, config.root)

    if json_output:
        payload = result.to_dict()
        payload["trace_path"] = shown_trace
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    typer.echo(result.answer)
    if result.citations:
        typer.echo("\nCitations:")
        for citation in result.citations:
            sources = f" ({', '.join(citation.source_ids)})" if citation.source_ids else ""
            typer.echo(f"- {citation.path}{sources}")
    if result.warnings:
        typer.secho("\nWarnings:", fg=typer.colors.YELLOW)
        for warning in result.warnings:
            typer.secho(f"- {warning}", fg=typer.colors.YELLOW)
    budget = result.trace["budget"]
    typer.echo(
        f"\ntrace: {shown_trace} "
        f"({len(result.trace['tools'])} tool call(s), {budget['spent_chars']} chars, "
        f"~{budget['spent_tokens']} tokens)"
    )


@app.command()
def chat(
    bundle: list[str] = typer.Option(
        [], "--bundle", help="Bundle path or cloud:<bundle-id>; repeatable."
    ),
    mode: str | None = typer.Option(None, "--mode", help="Retrieval condition: pd or rag."),
    resume: str | None = typer.Option(None, "--resume", help="Resume a conversation by ID."),
) -> None:
    """Interactive revision-pinned REPL; state in .oknoll/conversations/ (design §5.2)."""
    if mode is not None and mode not in ("pd", "rag"):
        typer.secho(f"oknoll chat: unknown mode {mode!r} (pd|rag)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if any(ref.startswith("cloud:") for ref in bundle):
        _not_yet("chat (cloud bundles)", "Phase 8 (hosted control plane)")
    if len(bundle) > 1:
        # The grammar stays stable; the capability is deliberately absent (ADR-0005).
        raise _fail(
            "oknoll chat: multi-bundle chat is out of scope (ADR-0005) — pass at most one --bundle"
        )

    config = _load_project()
    provider = _provider_for(config)
    bundle_path = Path(bundle[0]).resolve() if bundle else config.bundle_path

    current = read_current_revision_id(bundle_path)
    if current is None:
        raise _fail("no published revision to explore — run `oknoll build` first")

    if resume is not None:
        try:
            conversation = conversations.load(bundle_path, resume)
        except conversations.ConversationError as exc:
            raise _fail(f"oknoll chat: {exc}") from exc
        if conversation.revision_id != current:
            raise _fail(
                f"oknoll chat: conversation {conversation.id} is pinned to revision "
                f"{conversation.revision_id}, but the bundle is now at {current} — "
                "start a new conversation"
            )
        if mode is not None and mode != conversation.mode:
            raise _fail(
                f"oknoll chat: conversation {conversation.id} uses mode "
                f"{conversation.mode!r}; repeated arguments must match (design §5.2)"
            )
        typer.secho(
            f"resumed {conversation.id} — revision {current}, mode {conversation.mode}, "
            f"{conversation.turns} prior turn(s)",
            fg=typer.colors.GREEN,
        )
    else:
        conversation = conversations.create(bundle_path, revision_id=current, mode=mode or "pd")
        typer.secho(
            f"conversation {conversation.id} — revision {current}, mode {conversation.mode} "
            f"(resume with `oknoll chat --resume {conversation.id}`)",
            fg=typer.colors.GREEN,
        )

    embedder = _embedder_for(config) if conversation.mode == "rag" else None
    typer.echo("ask questions; empty line or `exit` to quit")
    while True:
        try:
            question = typer.prompt("you", prompt_suffix="> ", default="", show_default=False)
        except (typer.Abort, EOFError):
            break
        question = question.strip()
        if not question or question.lower() in ("exit", "quit", "/exit", "/quit"):
            break

        # A conversation is pinned to one revision; a rebuild in another terminal
        # would otherwise answer later turns from a different revision while the
        # meta line still claims the old one. Re-check every turn and stop rather
        # than record a turn that violates the file's own pin (design §5.2).
        live = read_current_revision_id(bundle_path)
        if live != conversation.revision_id:
            typer.secho(
                f"bundle revision changed ({conversation.revision_id} → {live}) — "
                f"this conversation is pinned; start a new one to continue",
                fg=typer.colors.RED,
                err=True,
            )
            break

        conversations.append_turn(conversation, "user", {"text": question})
        try:
            result = answer_question(
                bundle_dir=bundle_path,
                question=question,
                provider=provider,
                today=date.today().isoformat(),
                condition=conversation.mode,
                embedder=embedder,
            )
        except (ExplorerError, ValueError, ProviderError) as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            conversations.append_turn(conversation, "error", {"text": str(exc)})
            continue

        trace_path = write_trace(bundle_path, result)
        typer.echo(result.answer)
        for citation in result.citations:
            typer.echo(f"  - {citation.path}")
        for warning in result.warnings:
            typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)
        conversations.append_turn(
            conversation,
            "assistant",
            {
                "text": result.answer,
                "citations": [c.path for c in result.citations],
                "warnings": list(result.warnings),
                "trace_path": _display_path(trace_path, config.root),
            },
        )
    typer.echo(f"conversation saved: {_display_path(conversation.path, config.root)}")


@app.command()
def serve(
    mcp: bool = typer.Option(False, "--mcp", help="Serve the read-only stdio MCP interface."),
    bundle: Path | None = typer.Option(
        None,
        "--bundle",
        help="Bundle directory to serve (default: the active project's bundle). "
        "Works on any bundle, including read-only foreign ones.",
    ),
) -> None:
    """Serve the seven deterministic navigation tools over stdio MCP (design §10)."""
    if not mcp:
        typer.secho("oknoll serve: only --mcp is supported", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    from oknoll_cli.mcp_server import run_stdio

    bundle_root = bundle if bundle is not None else _load_project().bundle_path
    # stdout is the MCP transport from here on — status goes to stderr only.
    typer.secho(f"serving {bundle_root} over stdio MCP (ctrl-c to stop)", err=True)
    try:
        run_stdio(bundle_root, today=date.today().isoformat())
    except ValueError as exc:
        raise _fail(f"oknoll serve: {exc}") from exc
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None


@app.command()
def pack(
    profile: str = typer.Option(
        "okf", "--profile", help="okf (full) or plain (OKF-specific fields stripped)."
    ),
) -> None:
    """Produce a deterministic archive + manifest + checksums."""
    if profile not in ("okf", "plain"):
        typer.secho(
            f"oknoll pack: unknown profile {profile!r} (okf|plain)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    config = _load_project()
    current = read_current_revision_id(config.bundle_path)
    if current is None:
        raise _fail("no published revision to pack — run `oknoll build` first")

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


@app.command()
def diff(
    check: bool = typer.Option(
        False, "--check", help="Rebuild to temp and report reproducibility drift."
    ),
) -> None:
    """Report reproducibility drift; non-zero exit for CI."""
    if not check:
        typer.secho("oknoll diff: only --check is supported", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    config = _load_project()
    provider = _provider_for(config)
    sources = _pipeline_sources(config)
    try:
        current, drift = check_reproducibility(
            bundle_dir=config.bundle_path,
            project_name=config.name,
            sources=sources,
            provider=provider,
        )
    except (PipelineError, ConnectorError, ProviderError) as exc:
        raise _fail(f"oknoll diff: {exc}") from exc

    if drift:
        for path, kind in drift:
            typer.secho(f"drift    {kind:24} {path}", fg=typer.colors.RED)
        raise _fail(f"rebuild does not reproduce revision {current} ({len(drift)} file(s) drift)")
    typer.secho(f"reproducible — rebuild matches revision {current}", fg=typer.colors.GREEN)


@app.command()
def login() -> None:
    """Device-flow sign-in; refresh token in the OS keychain."""
    _not_yet("login", "Phase 8")


@app.command(name="eval")
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

    load_env(Path.cwd())
    try:
        benchmark = load_benchmark(benchmark_file)
    except BenchmarkError as exc:
        raise _fail(f"oknoll eval: {exc}") from exc

    try:
        answer_provider = resolve_provider_spec(model)
        rag_embedder = resolve_embedder_spec(embedder)
    except (ValueError, ProviderError) as exc:
        raise _fail(f"oknoll eval: {exc}") from exc

    try:
        results = run_benchmark(
            benchmark,
            provider=answer_provider,
            embedder=rag_embedder,
            today=date.today().isoformat(),
        )
    except (ExplorerError, ValueError, ProviderError) as exc:
        raise _fail(f"oknoll eval: {exc}") from exc

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


@plugin_app.command("list")
def plugin_list() -> None:
    """List installed connector plugins."""
    for name in plugins.INSTALLED:
        info = plugins.describe(plugins.get(name))
        typer.echo(f"{info['id']:8} {info['version']:8} {info['summary']}")


@plugin_app.command("inspect")
def plugin_inspect(name: str = typer.Argument(...)) -> None:
    """Inspect one installed connector plugin."""
    try:
        info = plugins.describe(plugins.get(name))
    except plugins.PluginError as exc:
        raise _fail(f"oknoll plugin inspect: {exc}") from exc
    for key in ("id", "version", "module", "summary"):
        typer.echo(f"{key:12} {info[key]}")
    typer.echo(f"{'capabilities':12} {', '.join(info['capabilities']) or '(none declared)'}")


@plugin_app.command("validate")
def plugin_validate(name: str = typer.Argument(...)) -> None:
    """Validate one installed connector plugin against the §6.2 protocol."""
    try:
        problems = plugins.validate(plugins.get(name))
    except plugins.PluginError as exc:
        raise _fail(f"oknoll plugin validate: {exc}") from exc
    if problems:
        for problem in problems:
            typer.secho(f"problem  {problem}", fg=typer.colors.RED)
        raise _fail(f"{name}: {len(problems)} protocol problem(s)")
    typer.secho(
        f"{name}: conforms to the connector protocol (behavioral contract is "
        "covered by its ConnectorContractSuite tests)",
        fg=typer.colors.GREEN,
    )


@keys_app.command("create")
def keys_create() -> None:
    """Create a scoped API key."""
    _not_yet("keys create", "Phase 9 (stretch)")


@keys_app.command("list")
def keys_list() -> None:
    """List API keys."""
    _not_yet("keys list", "Phase 9 (stretch)")


@keys_app.command("revoke")
def keys_revoke(key_id: str = typer.Argument(...)) -> None:
    """Revoke an API key."""
    _not_yet("keys revoke", "Phase 9 (stretch)")


if __name__ == "__main__":
    app()

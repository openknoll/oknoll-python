"""`oknoll query` — ask questions against bundles with citations and traces."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import typer
from okf_core import (
    AskResult,
    ExplorerError,
    answer_question,
    read_current_revision_id,
    write_trace,
)
from oknoll_providers import ProviderError
from oknoll_runtime import InstallError, StoreError, installed_tree, runtime_dirs

from oknoll_cli import conversations
from oknoll_cli.commands.common import (
    display_path,
    embedder_for,
    embedder_without_project,
    fail,
    load_project,
    not_yet,
    parse_cli_locator,
    provider_for,
    provider_without_project,
    resolve_installed,
    runtime_store_catalog,
)

query_app = typer.Typer(
    help="One-shot and conversational question answering over bundle revisions.",
    no_args_is_help=True,
)


@query_app.command("ask")
def ask(
    target_or_question: str = typer.Argument(
        ...,
        metavar="[TARGET] QUESTION",
        help=(
            "With one argument: the question, answered over the current project. "
            "With two: a bundle target first — an installed alias (handbook, "
            "local:handbook) or a bundle directory path — then the question."
        ),
    ),
    question_arg: str | None = typer.Argument(None, hidden=True),
    json_output: bool = typer.Option(
        False, "--json", help="Emit answer, citations, warnings, and trace as JSON."
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=(
            "Override the configured build.model for this question, e.g. "
            "anthropic:claude-opus-5 or ollama:llama3."
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
    """One-shot answer over the current project, an installed bundle, or a path."""
    if mode not in ("pd", "rag"):
        typer.secho(
            f"oknoll query ask: unknown mode {mode!r} (pd|rag)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    if question_arg is None:
        target, question = None, target_or_question
    else:
        target, question = target_or_question, question_arg

    if target is not None:
        _ask_target(
            target, question, json_output=json_output, model=model, mode=mode, embedder=embedder
        )
        return

    config = load_project()
    provider = provider_for(config, override=model)

    if read_current_revision_id(config.bundle_path) is None:
        raise fail("no published revision to explore — run `oknoll project build` first")

    try:
        result = answer_question(
            bundle_dir=config.bundle_path,
            question=question,
            provider=provider,
            today=date.today().isoformat(),
            condition=mode,
            embedder=embedder_for(config, override=embedder) if mode == "rag" else None,
        )
    except (ExplorerError, ValueError, ProviderError) as exc:
        raise fail(f"oknoll query ask: {exc}") from exc

    trace_path = write_trace(config.bundle_path, result)
    # An absolute [paths].bundle puts the trace outside the project root.
    shown_trace = display_path(trace_path, config.root)

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


def _ask_target(
    target: str,
    question: str,
    *,
    json_output: bool,
    model: str | None,
    mode: str,
    embedder: str | None,
) -> None:
    """Answer over a locator target: installed alias or unmanaged bundle path."""
    locator = parse_cli_locator(target)
    if locator.kind in ("oci", "oknoll"):
        not_yet("query ask (remote locators)", "the registry client")

    identity: str | None = None
    alias: str | None = None
    if locator.kind == "path":
        tree = Path(locator.path or "").expanduser()
        if not tree.is_dir():
            raise fail(f"bundle directory not found: {tree}")
    else:
        store, catalog = runtime_store_catalog()
        entry = resolve_installed(catalog, target)
        try:
            tree = installed_tree(store, entry)
        except (InstallError, StoreError) as exc:
            raise fail(f"oknoll query ask: {exc}") from exc
        alias = entry.alias
        identity = f"{entry.alias}@{entry.oci_digest} ({entry.okf_revision})"

    provider = provider_without_project(model)
    try:
        result = answer_question(
            bundle_dir=tree,
            question=question,
            provider=provider,
            today=date.today().isoformat(),
            condition=mode,
            embedder=embedder_without_project(embedder) if mode == "rag" else None,
        )
    except (ExplorerError, ValueError, ProviderError) as exc:
        raise fail(f"oknoll query ask: {exc}") from exc

    if alias is not None:
        _, catalog = runtime_store_catalog()
        catalog.touch_last_used(alias)
        trace_path = _write_runtime_trace(alias, result)
    else:
        trace_path = write_trace(tree, result)

    if json_output:
        payload = result.to_dict()
        payload["trace_path"] = str(trace_path)
        if identity is not None:
            payload["bundle"] = identity
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if identity is not None:
        typer.secho(identity, fg=typer.colors.BLUE)
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
    typer.echo(f"\ntrace: {trace_path}")


def _write_runtime_trace(alias: str, result: AskResult) -> Path:
    """Store trees are immutable, so locator-ask traces land in the data dir."""
    traces = runtime_dirs().data / "traces" / alias
    traces.mkdir(parents=True, exist_ok=True)
    stamp = str(result.trace.get("started_at", "")).replace(":", "").replace("-", "")
    base = f"ask-{stamp}"
    path = traces / f"{base}.json"
    counter = 2
    while path.exists():
        path = traces / f"{base}-{counter}.json"
        counter += 1
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _daemon_chat(targets: list[str], *, mode: str) -> None:
    """Multi-turn chat against the running daemon; one pinned session per alias."""
    import httpx
    from oknoll_runtime.daemon import daemon_url, read_record, read_token

    dirs = runtime_dirs()
    record = read_record(dirs.state)
    token = read_token(dirs.state)
    if record is None or token is None:
        raise fail(
            "oknoll query chat: the daemon is not running — start it with `oknoll daemon start`"
        )

    aliases: list[str] = []
    for target in targets:
        locator = parse_cli_locator(target)
        alias = locator.name or ""
        if alias not in aliases:
            aliases.append(alias)

    sessions: dict[str, dict[str, Any]] = {}
    with httpx.Client(
        base_url=daemon_url(record),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(180.0, connect=5.0),
    ) as client:
        for alias in aliases:
            try:
                response = client.post("/api/v1/sessions", json={"bundle": alias, "mode": mode})
            except httpx.HTTPError as exc:
                raise fail(f"oknoll query chat: cannot reach the daemon: {exc}") from exc
            body = response.json()
            if response.status_code != 201:
                raise fail(f"oknoll query chat: {body.get('message', 'session refused')}")
            sessions[alias] = body
            typer.secho(
                f"{alias}@{body['oci_digest']} ({body['okf_revision']}) — "
                f"session {body['session']}, mode {body['mode']}",
                fg=typer.colors.GREEN,
            )

        current = aliases[0]
        typer.echo(
            "ask questions; prefix `@alias ` to address another bundle; empty line "
            "or `exit` to quit"
        )
        while True:
            try:
                question = typer.prompt(
                    f"you[{current}]", prompt_suffix="> ", default="", show_default=False
                )
            except (typer.Abort, EOFError):
                break
            question = question.strip()
            if not question or question.lower() in ("exit", "quit", "/exit", "/quit"):
                break
            if question == "/bundles":
                for alias in aliases:
                    marker = "*" if alias == current else " "
                    typer.echo(f"{marker} {alias}")
                continue
            if question.startswith("@"):
                prefix, _, rest = question.partition(" ")
                alias = prefix[1:]
                if alias not in sessions:
                    typer.secho(
                        f"no session for {alias!r} (have: {', '.join(aliases)})",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    continue
                current = alias
                question = rest.strip()
                if not question:
                    continue

            try:
                response = client.post(
                    "/api/v1/ask",
                    json={"session": sessions[current]["session"], "question": question},
                )
            except httpx.HTTPError as exc:
                typer.secho(f"error: cannot reach the daemon: {exc}", fg=typer.colors.RED, err=True)
                continue
            body = response.json()
            if response.status_code != 200:
                typer.secho(
                    f"error: {body.get('message', 'ask failed')}", fg=typer.colors.RED, err=True
                )
                continue
            typer.secho(body["bundle"], fg=typer.colors.BLUE)
            typer.echo(body["answer"])
            for citation in body.get("citations", []):
                typer.echo(f"  - {citation.get('qualified', citation.get('path'))}")
            for warning in body.get("warnings", []):
                typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)


@query_app.command("chat")
def chat(
    bundle: list[str] = typer.Option(
        [],
        "--bundle",
        help="Bundle to chat with: a directory path (single, in-process) or an "
        "installed alias (repeatable — served by the running daemon).",
    ),
    mode: str | None = typer.Option(None, "--mode", help="Retrieval condition: pd or rag."),
    resume: str | None = typer.Option(None, "--resume", help="Resume a conversation by ID."),
) -> None:
    """Interactive revision-pinned REPL; state in .oknoll/conversations/."""
    if mode is not None and mode not in ("pd", "rag"):
        typer.secho(
            f"oknoll query chat: unknown mode {mode!r} (pd|rag)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    kinds = [parse_cli_locator(target).kind for target in bundle]
    if any(kind in ("oci", "oknoll") for kind in kinds):
        not_yet("query chat (remote locators)", "the registry client")
    daemon_targets = [
        target for target, kind in zip(bundle, kinds, strict=True) if kind in ("bare", "local")
    ]
    if daemon_targets or len(bundle) > 1:
        # Aliases (and any multi-bundle chat) are served by the daemon, which
        # pins each session to the installed revision.
        if len(daemon_targets) != len(bundle):
            raise fail(
                "oknoll query chat: daemon chat serves installed bundles — pass "
                "aliases only (mixing directory paths is not supported)"
            )
        if resume is not None:
            raise fail(
                "oknoll query chat: --resume applies to project chat — daemon "
                "sessions are per-invocation"
            )
        _daemon_chat(daemon_targets, mode=mode or "pd")
        return

    config = load_project()
    provider = provider_for(config)
    bundle_path = Path(bundle[0]).resolve() if bundle else config.bundle_path

    current = read_current_revision_id(bundle_path)
    if current is None:
        raise fail("no published revision to explore — run `oknoll project build` first")

    if resume is not None:
        try:
            conversation = conversations.load(bundle_path, resume)
        except conversations.ConversationError as exc:
            raise fail(f"oknoll query chat: {exc}") from exc
        if conversation.revision_id != current:
            raise fail(
                f"oknoll query chat: conversation {conversation.id} is pinned to revision "
                f"{conversation.revision_id}, but the bundle is now at {current} — "
                "start a new conversation"
            )
        if mode is not None and mode != conversation.mode:
            raise fail(
                f"oknoll query chat: conversation {conversation.id} uses mode "
                f"{conversation.mode!r}; repeated arguments must match"
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
            f"(resume with `oknoll query chat --resume {conversation.id}`)",
            fg=typer.colors.GREEN,
        )

    embedder = embedder_for(config) if conversation.mode == "rag" else None
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
        # than record a turn that violates the file's own pin.
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
                "trace_path": display_path(trace_path, config.root),
            },
        )
    typer.echo(f"conversation saved: {display_path(conversation.path, config.root)}")

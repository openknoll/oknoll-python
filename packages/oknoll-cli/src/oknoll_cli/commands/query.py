"""`oknoll query` — ask questions against bundles with citations and traces."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import typer
from okf_core import (
    ExplorerError,
    answer_question,
    read_current_revision_id,
    write_trace,
)
from oknoll_providers import ProviderError

from oknoll_cli import conversations
from oknoll_cli.commands.common import (
    display_path,
    embedder_for,
    fail,
    load_project,
    provider_for,
)

query_app = typer.Typer(
    help="One-shot and conversational question answering over bundle revisions.",
    no_args_is_help=True,
)


@query_app.command("ask")
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
    """One-shot answer over the current bundle revision."""
    if mode not in ("pd", "rag"):
        typer.secho(
            f"oknoll query ask: unknown mode {mode!r} (pd|rag)", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

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


@query_app.command("chat")
def chat(
    bundle: list[str] = typer.Option(
        [], "--bundle", help="Bundle directory path; repeatable once the daemon lands."
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
    if len(bundle) > 1:
        # The grammar stays stable; the capability arrives with the daemon.
        raise fail(
            "oknoll query chat: multi-bundle chat needs the daemon — pass at most one --bundle"
        )

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

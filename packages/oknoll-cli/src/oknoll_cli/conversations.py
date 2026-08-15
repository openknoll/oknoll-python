"""Revision-pinned chat conversations — derived local state.

One conversation = one JSONL file under `<bundle>/.oknoll/conversations/`:
a `meta` line pinning scope (bundle, revision, mode) followed by `turn` lines.
Derived state, never bundle content, never packed — same standing as traces.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONVERSATIONS_DIR = ".oknoll/conversations"

# Resume ids come from the command line — the charset is the path validation.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


class ConversationError(ValueError):
    """Bad conversation id, missing conversation, or corrupt state file."""


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    path: Path
    revision_id: str
    mode: str
    turns: int


def _dir(bundle: Path) -> Path:
    return bundle / CONVERSATIONS_DIR


def _path(bundle: Path, conversation_id: str) -> Path:
    if not _ID_RE.match(conversation_id):
        raise ConversationError(f"invalid conversation id: {conversation_id!r}")
    return _dir(bundle) / f"{conversation_id}.jsonl"


def create(bundle: Path, *, revision_id: str, mode: str) -> Conversation:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    conversation_id = f"chat-{stamp}-{secrets.token_hex(2)}"
    path = _path(bundle, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "id": conversation_id,
        "revision_id": revision_id,
        "mode": mode,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
    return Conversation(id=conversation_id, path=path, revision_id=revision_id, mode=mode, turns=0)


def load(bundle: Path, conversation_id: str) -> Conversation:
    path = _path(bundle, conversation_id)
    if not path.is_file():
        raise ConversationError(f"no such conversation: {conversation_id}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0]) if lines else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversationError(f"corrupt conversation state: {path}") from exc
    if (
        not isinstance(meta, dict)
        or meta.get("type") != "meta"
        or "revision_id" not in meta
        or "mode" not in meta
    ):
        raise ConversationError(f"corrupt conversation state: {path}")
    # Only user turns count toward the "N prior turn(s)" figure — an error line
    # or an assistant reply is not a turn the user took.
    turns = sum(1 for line in lines[1:] if line.strip() and _is_user_turn(line))
    return Conversation(
        id=conversation_id,
        path=path,
        revision_id=str(meta["revision_id"]),
        mode=str(meta["mode"]),
        turns=turns,
    )


def _is_user_turn(line: str) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(record, dict) and record.get("role") == "user"


def append_turn(conversation: Conversation, role: str, payload: dict[str, Any]) -> None:
    record = {"type": "turn", "role": role, **payload}
    with conversation.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_turns(conversation: Conversation) -> list[dict[str, Any]]:
    """Parsed turn records, transcript order; malformed lines are skipped with
    the same tolerance as the turn counter."""
    try:
        lines = conversation.path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("type") == "turn":
            records.append(record)
    return records


def qa_history(conversation: Conversation) -> tuple[list[dict[str, str]], list[str]]:
    """(question/answer pairs oldest→newest, the last answer's citation paths).

    The transcript is the only state chat has between turns — this pairs each
    user turn with the assistant turn that answered it (error turns leave a
    question unpaired and it is dropped) and surfaces the most recent answer's
    citations for retrieval carryover.
    """
    turns: list[dict[str, str]] = []
    carryover: list[str] = []
    pending: str | None = None
    for record in read_turns(conversation):
        role = record.get("role")
        if role == "user":
            pending = str(record.get("text", ""))
        elif role == "assistant":
            if pending is not None:
                turns.append({"question": pending, "answer": str(record.get("text", ""))})
                pending = None
            citations = record.get("citations")
            carryover = [str(c) for c in citations] if isinstance(citations, list) else []
    return turns, carryover

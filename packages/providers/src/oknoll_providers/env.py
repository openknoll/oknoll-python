"""Minimal `.env` loader — project-root convention, never overrides the real environment.

Deliberately not python-dotenv: the format we support is the plain `KEY=VALUE`
subset (blank lines, `#` comments, an optional `export ` prefix, quoted values,
and unquoted values with a trailing ` # comment`). Values already present in
``os.environ`` always win, so an exported shell variable beats the file.

This runs for every project-scoped CLI command, so a malformed or hostile file
must produce a clean ``ProviderError`` (one-line exit) — never a traceback.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from oknoll_providers.errors import ProviderError

ENV_FILE_NAME = ".env"
_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ENV_BYTES = 256 * 1024


def load_env(root: Path) -> None:
    """Load ``root/.env`` into ``os.environ`` without overriding existing values."""
    path = root / ENV_FILE_NAME
    if not path.is_file():
        return
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProviderError(f"cannot read {path}: {exc}") from exc
    if len(raw) > _MAX_ENV_BYTES:
        raise ProviderError(f"{path} is larger than {_MAX_ENV_BYTES // 1024} KiB — not a .env file")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProviderError(f"{path} is not valid UTF-8: {exc}") from exc

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not _KEY_PATTERN.match(key):
            continue
        parsed = _parse_value(value.strip())
        if parsed is None or "\x00" in parsed:
            continue
        os.environ.setdefault(key, parsed)


def _parse_value(value: str) -> str | None:
    if value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        if closing == -1:
            return None  # unterminated quote — skip the line
        return value[1:closing]  # anything after the closing quote is a comment
    return value.split(" #", 1)[0].strip()

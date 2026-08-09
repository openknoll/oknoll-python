"""Machine-level configuration: ``~/.oknoll/config.toml`` + ``~/.oknoll/.env``.

Two planes, deliberately separate:

- **Settings** (model/embedder defaults, Ollama endpoint) live in
  ``config.toml`` and resolve *specific beats general*:
  CLI flag > bundle ``oknoll.toml`` > ``~/.oknoll/config.toml`` > ``"stub"``.
- **Secrets** (API keys) never appear in any TOML. They flow through the
  environment only: shell env > project ``.env`` > ``~/.oknoll/.env`` —
  the ordering falls out of ``load_env``'s setdefault semantics.

``config.toml`` mirrors the bundle file's section names (``[build]``,
``[rag]``, plus ``[providers.ollama]``) so the docs are one table. The home
directory is ``~/.oknoll`` unless ``OKNOLL_HOME`` says otherwise.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from oknoll_providers import load_env

CONFIG_FILE = "config.toml"
HOME_ENV = "OKNOLL_HOME"
OLLAMA_HOST_ENV = "OLLAMA_HOST"


class GlobalConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    model: str | None = None
    embedder: str | None = None
    ollama_host: str | None = None


def oknoll_home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".oknoll"


def load_global_config(home: Path | None = None) -> GlobalConfig:
    """Read ``config.toml`` under `home`; a missing file is an empty config."""
    path = (home or oknoll_home()) / CONFIG_FILE
    if not path.is_file():
        return GlobalConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GlobalConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise GlobalConfigError(f"{path} is not valid TOML: {exc}") from exc

    def _get(table: object, key: str) -> str | None:
        if not isinstance(table, dict):
            return None
        value = table.get(key)
        return value if isinstance(value, str) and value else None

    providers = data.get("providers")
    ollama = providers.get("ollama") if isinstance(providers, dict) else None
    return GlobalConfig(
        model=_get(data.get("build"), "model"),
        embedder=_get(data.get("rag"), "embedder"),
        ollama_host=_get(ollama, "host"),
    )


def apply_global_env(home: Path | None = None) -> None:
    """Layer the machine-level env under whatever is already set.

    Loads ``~/.oknoll/.env`` (never overriding existing values, so the shell
    and a project ``.env`` loaded earlier both win), then defaults
    ``OLLAMA_HOST`` from ``[providers.ollama].host`` — weakest of all, since
    it only applies when no env layer set the variable.
    """
    home = home or oknoll_home()
    load_env(home)
    host = load_global_config(home).ollama_host
    if host:
        os.environ.setdefault(OLLAMA_HOST_ENV, host)

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
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oknoll_providers import DEFAULT_OLLAMA_HOST, load_env

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


def display(path: Path) -> str:
    """`path` with the home directory collapsed to ``~`` — for messages."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


# --- `oknoll config` surface -----------------------------------------------
# The global config.toml is tool-owned: `config set` rewrites it, preserving
# every parsed key (hand-added ones included) but not comments.


def _validate_model(spec: str) -> str | None:
    if spec == "stub":
        return None
    scheme, separator, model = spec.partition(":")
    if scheme == "anthropic":
        if separator and not model.strip():
            return "empty model after 'anthropic:' — use 'anthropic' or 'anthropic:<model>'"
        return None
    if scheme == "ollama":
        return (
            None
            if model.strip()
            else "the ollama provider needs an explicit model, e.g. 'ollama:llama3'"
        )
    return (
        f"unknown model provider {spec!r} — available: "
        "'stub', 'anthropic[:<model>]', 'ollama:<model>'"
    )


def _validate_embedder(spec: str) -> str | None:
    if spec == "stub":
        return None
    scheme, _, model = spec.partition(":")
    if scheme == "ollama":
        return (
            None
            if model.strip()
            else "the ollama embedder needs an explicit model, e.g. 'ollama:nomic-embed-text'"
        )
    return (
        f"unknown embedding provider {spec!r} — available: 'stub', 'ollama:<model>' "
        "(Anthropic has no embeddings API)"
    )


def _validate_host(value: str) -> str | None:
    if not value or any(ch.isspace() for ch in value):
        return f"invalid host {value!r} — expected a URL like {DEFAULT_OLLAMA_HOST}"
    return None


SETTINGS: dict[str, tuple[tuple[str, ...], Callable[[str], str | None]]] = {
    "build.model": (("build", "model"), _validate_model),
    "rag.embedder": (("rag", "embedder"), _validate_embedder),
    "providers.ollama.host": (("providers", "ollama", "host"), _validate_host),
}

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_HEADER = (
    "# oknoll machine-level configuration — rewritten by `oknoll config set`;\n"
    "# hand edits are kept, comments are not.\n"
)


def read_raw_config(home: Path | None = None) -> dict[str, Any]:
    path = (home or oknoll_home()) / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GlobalConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise GlobalConfigError(f"{path} is not valid TOML: {exc}") from exc


def _format_key(key: str) -> str:
    return (
        key if _BARE_KEY.match(key) else '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    )


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    raise GlobalConfigError(f"cannot write a {type(value).__name__} value to {CONFIG_FILE}")


def _emit(data: dict[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines: list[str] = []
    if prefix and (scalars or not tables):
        lines.append("[" + ".".join(_format_key(part) for part in prefix) + "]")
    lines.extend(f"{_format_key(k)} = {_format_value(v)}" for k, v in scalars.items())
    for key, table in tables.items():
        if lines:
            lines.append("")
        lines.extend(_emit(table, (*prefix, key)))
    return lines


def write_raw_config(data: dict[str, Any], home: Path | None = None) -> Path:
    home = home or oknoll_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / CONFIG_FILE
    body = "\n".join(_emit(data))
    path.write_text(_HEADER + ("\n" + body + "\n" if body else ""), encoding="utf-8")
    return path


def _known_keys() -> str:
    return ", ".join(SETTINGS)


def set_setting(key: str, value: str, home: Path | None = None) -> Path:
    """Validate and write one setting into ``~/.oknoll/config.toml``."""
    entry = SETTINGS.get(key)
    if entry is None:
        raise GlobalConfigError(f"unknown setting {key!r} — available: {_known_keys()}")
    path_parts, validate = entry
    value = value.strip()
    error = validate(value)
    if error is not None:
        raise GlobalConfigError(error)
    data = read_raw_config(home)
    node = data
    for part in path_parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path_parts[-1]] = value
    return write_raw_config(data, home)


def unset_setting(key: str, home: Path | None = None) -> bool:
    """Remove one setting; returns False when it was not set. Prunes empty tables."""
    entry = SETTINGS.get(key)
    if entry is None:
        raise GlobalConfigError(f"unknown setting {key!r} — available: {_known_keys()}")
    path_parts = entry[0]
    data = read_raw_config(home)
    chain: list[dict[str, Any]] = [data]
    for part in path_parts[:-1]:
        child = chain[-1].get(part)
        if not isinstance(child, dict):
            return False
        chain.append(child)
    if path_parts[-1] not in chain[-1]:
        return False
    del chain[-1][path_parts[-1]]
    for depth in range(len(chain) - 1, 0, -1):
        if not chain[depth]:
            del chain[depth - 1][path_parts[depth - 1]]
    write_raw_config(data, home)
    return True


@dataclass(frozen=True, slots=True)
class EffectiveSetting:
    key: str
    value: str
    source: str


def effective_settings(
    bundle_model: str | None, bundle_embedder: str | None, home: Path | None = None
) -> list[EffectiveSetting]:
    """The settings a command would actually use, with per-key provenance."""
    home = home or oknoll_home()
    config = load_global_config(home)
    config_source = display(home / CONFIG_FILE)

    def pick(key: str, bundle_value: str | None, global_value: str | None) -> EffectiveSetting:
        if bundle_value is not None:
            return EffectiveSetting(key, bundle_value, "oknoll.toml")
        if global_value is not None:
            return EffectiveSetting(key, global_value, config_source)
        return EffectiveSetting(key, "stub", "default")

    # apply_global_env seeds OLLAMA_HOST from config.toml via setdefault, so an
    # env value equal to the config value is attributed to the config file.
    env_host = os.environ.get(OLLAMA_HOST_ENV)
    if env_host and env_host != config.ollama_host:
        host = EffectiveSetting(
            "providers.ollama.host", env_host, f"{OLLAMA_HOST_ENV} (environment or .env)"
        )
    elif config.ollama_host:
        host = EffectiveSetting("providers.ollama.host", config.ollama_host, config_source)
    else:
        host = EffectiveSetting("providers.ollama.host", DEFAULT_OLLAMA_HOST, "default")

    return [
        pick("build.model", bundle_model, config.model),
        pick("rag.embedder", bundle_embedder, config.embedder),
        host,
    ]

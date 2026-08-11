"""Project layer: `oknoll init` skeleton, config loading, source registration.

`oknoll.toml` is user-owned; source registration is append-only so comments and
formatting survive. Reading uses stdlib tomllib.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "oknoll.toml"
IGNORE_NAME = ".oknollignore"


class ProjectExistsError(RuntimeError):
    pass


class ProjectError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredSource:
    connector: str
    uri: str


@dataclass(slots=True)
class ProjectConfig:
    root: Path
    name: str
    sources_dir: str = "sources"
    bundle_dir: str = "bundle"
    # None means "not set in oknoll.toml" — resolution falls through to
    # ~/.oknoll/config.toml and then the "stub" default (see main._provider_for).
    model: str | None = None
    embedder: str | None = None
    # Regeneration knob: bumping it invalidates every cached model generation
    # for this bundle, so the next build re-generates as a reviewable revision.
    generation_version: str = "0"
    strict: bool = False
    sources: list[RegisteredSource] = field(default_factory=list)

    @property
    def bundle_path(self) -> Path:
        return self.root / self.bundle_dir


def find_project_root(start: Path) -> Path | None:
    """Walk up from `start` to the first directory containing oknoll.toml."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    return None


def load_project(root: Path) -> ProjectConfig:
    config_path = root / CONFIG_NAME
    if not config_path.is_file():
        raise ProjectError(f"{config_path} not found — run `oknoll init` first")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"{config_path} is not valid TOML: {exc}") from exc

    project = data.get("project", {})
    paths = data.get("paths", {})
    build = data.get("build", {})
    rag = data.get("rag", {})
    lint = data.get("lint", {})
    sources: list[RegisteredSource] = []
    for entry in data.get("sources", []):
        if not isinstance(entry, dict):
            continue
        connector = entry.get("connector")
        uri = entry.get("uri")
        if isinstance(connector, str) and isinstance(uri, str):
            sources.append(RegisteredSource(connector=connector, uri=uri))

    model = build.get("model")
    embedder = rag.get("embedder")
    generation_version = build.get("generation_version", "0")
    if isinstance(generation_version, bool) or not isinstance(generation_version, (str, int)):
        raise ProjectError(f"{config_path}: [build].generation_version must be a string or integer")
    return ProjectConfig(
        root=root,
        name=str(project.get("name", root.name)),
        sources_dir=str(paths.get("sources", "sources")),
        bundle_dir=str(paths.get("bundle", "bundle")),
        model=str(model) if model is not None else None,
        embedder=str(embedder) if embedder is not None else None,
        generation_version=str(generation_version),
        strict=bool(lint.get("strict", False)),
        sources=sources,
    )


def register_source(root: Path, connector: str, uri: str) -> bool:
    """Append a [[sources]] entry to oknoll.toml; returns False when already present."""
    config = load_project(root)
    if RegisteredSource(connector=connector, uri=uri) in config.sources:
        return False
    config_path = root / CONFIG_NAME
    existing = config_path.read_text(encoding="utf-8")
    separator = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"
    block = f'{separator}[[sources]]\nconnector = "{connector}"\nuri = "{uri}"\n'
    config_path.write_text(existing + block, encoding="utf-8")
    return True


def ignore_patterns(root: Path) -> tuple[str, ...]:
    ignore_path = root / IGNORE_NAME
    if not ignore_path.is_file():
        return ()
    patterns = []
    for line in ignore_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return tuple(patterns)


def _config_text(name: str) -> str:
    return f"""\
# oknoll project configuration (created by `oknoll init`)

[project]
name = "{name}"

[paths]
sources = "sources"
bundle = "bundle"

[build]
# Model provider for generation and `oknoll ask`. Precedence:
#   `oknoll ask --model SPEC` > this file > ~/.oknoll/config.toml [build].model > "stub".
# "stub" is the deterministic CI provider; real providers:
#   model = "anthropic:claude-opus-5"   # needs ANTHROPIC_API_KEY (.env or ~/.oknoll/.env)
#   model = "ollama:llama3"             # needs a local `ollama serve`
# Unset here, so the machine default in ~/.oknoll/config.toml applies:
# model = "stub"
# Bump to deliberately regenerate all model-generated fields on the next
# build (e.g. after a provider alias started serving a better model). The
# rebuild publishes a new revision with a reviewable diff:
# generation_version = "1"

[rag]
# Embedding provider for `oknoll ask --mode rag` (the vector baseline). Precedence:
#   `oknoll ask --embedder SPEC` > this file > ~/.oknoll/config.toml [rag].embedder > "stub".
# "stub" is the deterministic CI embedder; a real one needs local Ollama:
#   embedder = "ollama:nomic-embed-text"   # needs a local `ollama serve`
# Unset here, so the machine default in ~/.oknoll/config.toml applies:
# embedder = "stub"

[lint]
strict = false
"""


_IGNORE_TEXT = """\
# Paths excluded from `oknoll add` source scanning
.git/
.oknoll/
.env
node_modules/
__pycache__/
.DS_Store
"""


def _index_text(name: str) -> str:
    return f"""\
---
okf_version: "0.2"
title: {name}
description: OKF bundle for {name}.
---

# {name}

No concepts yet. Run `oknoll add SOURCE` and `oknoll build` to populate this bundle.
"""


_LOG_TEXT = """\
# Revision log

No revisions yet.
"""


def init_project(path: Path, *, name: str | None = None, force: bool = False) -> list[str]:
    """Create the project skeleton; returns created paths relative to `path`."""
    path.mkdir(parents=True, exist_ok=True)
    config_path = path / CONFIG_NAME
    if config_path.exists() and not force:
        raise ProjectExistsError(
            f"{config_path} already exists (use --force to overwrite the config)"
        )

    project_name = name or path.resolve().name
    bundle_dir = path / "bundle"
    created: list[str] = []

    def write(target: Path, content: str) -> None:
        if target.exists() and not force and target != config_path:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(target.relative_to(path).as_posix())

    write(config_path, _config_text(project_name))
    write(path / IGNORE_NAME, _IGNORE_TEXT)
    write(bundle_dir / "index.md", _index_text(project_name))
    write(bundle_dir / "log.md", _LOG_TEXT)
    write(
        bundle_dir / "manifest.json",
        json.dumps({"okf_version": "0.2", "files": {}}, indent=2) + "\n",
    )

    for sub in ("sources", "bundle/concepts", "bundle/references"):
        target = path / sub
        if not target.exists():
            target.mkdir(parents=True)
            created.append(f"{sub}/")

    return created

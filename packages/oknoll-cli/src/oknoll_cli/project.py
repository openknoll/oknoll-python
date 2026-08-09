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
    model: str = "stub"
    embedder: str = "stub"
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

    return ProjectConfig(
        root=root,
        name=str(project.get("name", root.name)),
        sources_dir=str(paths.get("sources", "sources")),
        bundle_dir=str(paths.get("bundle", "bundle")),
        model=str(build.get("model", "stub")),
        embedder=str(rag.get("embedder", "stub")),
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
# Model provider for generation and `oknoll ask`. "stub" is the deterministic
# CI provider; real providers:
#   model = "anthropic:claude-opus-5"   # needs ANTHROPIC_API_KEY in .env
#   model = "ollama:llama3"             # needs a local `ollama serve`
# `oknoll ask --model SPEC` overrides this per question.
model = "stub"

[rag]
# Embedding provider for `oknoll ask --mode rag` (the vector baseline).
# "stub" is the deterministic CI embedder; a real one needs local Ollama:
#   embedder = "ollama:nomic-embed-text"   # needs a local `ollama serve`
# `oknoll ask --embedder SPEC` overrides this per question.
embedder = "stub"

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

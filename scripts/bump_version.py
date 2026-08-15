#!/usr/bin/env python3
"""Move the whole workspace to one version, in lockstep with the release tag.

Usage: uv run python scripts/bump_version.py 0.3.0

Rewrites, for every distributable package:
- `version = "..."` in its pyproject.toml
- sibling workspace dependencies to exact pins (`okf-core==0.3.0`) — in dev the
  uv workspace source still wins, so the pins only bite on a PyPI install,
  where they keep a release's six packages moving together
- the informational `__version__` strings in the package sources

The release workflow refuses to publish when the tag and these versions
disagree, so run this (and commit) before tagging `v<version>`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PACKAGE_DIRS = [
    REPO / "packages" / "okf-core",
    REPO / "packages" / "connectors",
    REPO / "packages" / "providers",
    REPO / "packages" / "eval",
    REPO / "packages" / "oknoll-runtime",
    REPO / "packages" / "oknoll-cli",
]

SIBLING_DISTS = [
    "okf-core",
    "oknoll-connectors",
    "oknoll-providers",
    "oknoll-eval",
    "oknoll-runtime",
    "oknoll",
]

VERSION_FILES = [
    REPO / "packages" / "okf-core" / "src" / "okf_core" / "_version.py",
    REPO / "packages" / "connectors" / "src" / "oknoll_connectors" / "__init__.py",
    REPO / "packages" / "providers" / "src" / "oknoll_providers" / "__init__.py",
    REPO / "packages" / "eval" / "src" / "oknoll_eval" / "__init__.py",
    REPO / "packages" / "oknoll-runtime" / "src" / "oknoll_runtime" / "__init__.py",
    REPO / "packages" / "oknoll-cli" / "src" / "oknoll_cli" / "__init__.py",
]

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _rewrite(path: Path, pattern: str, replacement: str, *, required: bool) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if required and count == 0:
        sys.exit(f"error: no match for {pattern!r} in {path}")
    if count:
        path.write_text(updated, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2 or not VERSION_RE.match(sys.argv[1]):
        sys.exit(f"usage: {sys.argv[0]} <major.minor.patch>")
    version = sys.argv[1]

    for package_dir in PACKAGE_DIRS:
        pyproject = package_dir / "pyproject.toml"
        _rewrite(pyproject, r'^version = "[^"]+"$', f'version = "{version}"', required=True)
        for dist in SIBLING_DISTS:
            _rewrite(
                pyproject,
                rf'^(\s+)"{re.escape(dist)}(?:==[^"]+)?",$',
                rf'\g<1>"{dist}=={version}",',
                required=False,
            )

    for source in VERSION_FILES:
        _rewrite(source, r'^__version__ = "[^"]+"$', f'__version__ = "{version}"', required=True)

    print(f"workspace at {version} — run `make install` to refresh uv.lock, then commit")


if __name__ == "__main__":
    main()

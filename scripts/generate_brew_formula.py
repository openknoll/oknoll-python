#!/usr/bin/env python3
"""Generate the Homebrew formula for the `oknoll` CLI and print it to stdout.

Usage: python scripts/generate_brew_formula.py 0.3.0 > Formula/oknoll.rb

Resolves the full dependency closure of `oknoll==<version>` from PyPI with a
`pip install --dry-run --report` (no packages are installed), then emits a
`virtualenv_install_with_resources` formula whose resources are the sdists of
every resolved distribution. The interpreter running this script must match
the formula's `depends_on "python@3.12"` so environment markers resolve the
same way they will at install time.

We deliberately do not shell out to `brew update-python-resources`: its
resolution excludes packages uploaded to PyPI within the last day, which is
exactly the window in which the release workflow runs. For the same reason a
just-published release may not have propagated to PyPI's index yet when this
script runs (the v0.3.5 tap job failed on exactly that race), so resolution
failures are retried until a deadline. The output is meant to be PR'd to the
openknoll/homebrew-tap repo, whose CI (`brew test-bot`) is the gate: audit,
source install, and `brew test`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# The release workflow reaches this script seconds after `pypa/gh-action-pypi-
# publish` returns, and PyPI's index propagates with a small delay. Ten minutes
# is far beyond any observed propagation lag while still failing the job fast
# enough to notice a genuinely missing release.
RESOLVE_TIMEOUT_SECONDS = 600
RESOLVE_RETRY_SECONDS = 20

FORMULA_HEAD = """\
class Oknoll < Formula
  include Language::Python::Virtualenv

  desc "Local-first CLI for building, linting, exploring, and packing OKF bundles"
  homepage "https://github.com/openknoll/oknoll-python"
  url "{url}"
  sha256 "{sha256}"
  license "Apache-2.0"

  depends_on "pkgconf" => :build
  depends_on "rust" => :build
  depends_on "libxml2"
  depends_on "libxslt"
  depends_on "openssl@3"
  depends_on "python@3.12"

  uses_from_macos "libffi"
"""

FORMULA_TAIL = """\
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/oknoll --version")
  end
end
"""


def sdist_for(name: str, version: str) -> tuple[str, str, str]:
    """Return (canonical name, sdist URL, sha256) for one PyPI release."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json") as f:
        data = json.load(f)
    sdist = next((u for u in data["urls"] if u["packagetype"] == "sdist"), None)
    if sdist is None:
        raise SystemExit(f"error: {name} {version} has no sdist on PyPI")
    # brew audit wants resource names in PyPI's dashed canonical form (e.g.
    # pydantic-core, whose metadata name is pydantic_core).
    canonical = re.sub(r"[-_.]+", "-", data["info"]["name"])
    return canonical, sdist["url"], sdist["digests"]["sha256"]


# pip's wording when a requested version is absent from the index — the only
# failure that PyPI propagation delay produces, and therefore the only one
# worth retrying. Anything else (no pip in the interpreter, network refusal,
# a yanked dependency) fails the same way on every attempt and must surface
# immediately, not after the deadline.
_RETRYABLE_MARKERS = (
    "No matching distribution found",
    "Could not find a version that satisfies",
)


def _resolve_once(version: str) -> list[tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--dry-run",
                "--ignore-installed",
                f"--report={report_path}",
                f"oknoll=={version}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(report_path.read_text())
    return [(i["metadata"]["name"], i["metadata"]["version"]) for i in report["install"]]


def resolve(
    version: str,
    *,
    timeout: float = RESOLVE_TIMEOUT_SECONDS,
    interval: float = RESOLVE_RETRY_SECONDS,
) -> list[tuple[str, str]]:
    """Resolve oknoll==<version> from PyPI; return [(name, version)] incl. oknoll.

    "No matching distribution" is retried until ``timeout``: right after
    publishing, PyPI's index may not serve the new version yet. Every other
    pip failure surfaces immediately with pip's own stderr.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _resolve_once(version)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            if not any(marker in detail for marker in _RETRYABLE_MARKERS):
                raise SystemExit(f"error: pip resolution failed:\n{detail}") from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SystemExit(
                    f"error: could not resolve oknoll=={version} within {timeout:.0f}s — "
                    "is the release actually on PyPI?"
                ) from None
            print(
                f"oknoll=={version} not on the index yet (propagation?); "
                f"retrying in {interval:.0f}s ({remaining:.0f}s left)",
                file=sys.stderr,
            )
            time.sleep(interval)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    version = sys.argv[1].removeprefix("v")

    resources = []
    head = None
    for name, resolved_version in resolve(version):
        canonical, url, sha256 = sdist_for(name, resolved_version)
        if canonical == "oknoll":
            if resolved_version != version:
                raise SystemExit(f"error: resolved oknoll {resolved_version}, wanted {version}")
            head = FORMULA_HEAD.format(url=url, sha256=sha256)
        else:
            resources.append((canonical, url, sha256))
    if head is None:
        raise SystemExit("error: oknoll itself missing from the pip resolution")

    blocks = [
        f'  resource "{name}" do\n    url "{url}"\n    sha256 "{sha256}"\n  end\n'
        for name, url, sha256 in sorted(resources, key=lambda r: r[0].lower())
    ]
    sys.stdout.write("\n".join([head, *blocks, FORMULA_TAIL]))


if __name__ == "__main__":
    main()

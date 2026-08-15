"""BundleToolkit: shared guardrail/error discipline + the cache-dir index."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from oknoll_runtime.toolkit import TOOL_NAMES, BundleToolkit, ToolkitError

REPO_ROOT = Path(__file__).resolve().parents[3]
MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    # Exploring writes derived state into the tree — always copy fixtures.
    dest = tmp_path / "bundle"
    shutil.copytree(MULTIHOP, dest)
    return dest


def test_seven_tools_return_explorer_shapes(bundle: Path) -> None:
    toolkit = BundleToolkit(bundle, today="2026-08-15")
    assert set(TOOL_NAMES) == {
        "overview",
        "list",
        "search",
        "peek",
        "read",
        "links",
        "history",
    }
    overview = toolkit.overview()
    assert overview["counts"]["concepts"] >= 1
    listing = toolkit.list("concepts")
    assert listing["total"] == len(listing["entries"]) or listing["total"] >= len(
        listing["entries"]
    )
    hits = toolkit.search("incident")
    assert any("incident" in hit["path"] for hit in hits["results"])
    path = hits["results"][0]["path"]
    assert toolkit.peek(path)["path"] == path
    assert toolkit.read(path)["path"] == path
    assert set(toolkit.links(path)) >= {"inbound", "outbound"}
    assert "entries" in toolkit.history()


def test_missing_bundle_raises_toolkit_error(tmp_path: Path) -> None:
    with pytest.raises(ToolkitError):
        BundleToolkit(tmp_path / "nope")


def test_guardrail_refusals_are_safe_and_named(bundle: Path) -> None:
    toolkit = BundleToolkit(bundle)
    with pytest.raises(ToolkitError) as excinfo:
        toolkit.read("../etc/passwd")
    message = str(excinfo.value)
    assert message.startswith("read:")
    assert str(bundle) not in message  # never the host path


def test_internal_errors_are_logged_not_echoed(bundle: Path) -> None:
    logged: list[str] = []
    toolkit = BundleToolkit(bundle, on_internal_error=logged.append)
    # Force an internal failure: vanish a file after the session snapshot.
    listing = toolkit.list("concepts")
    victim = listing["entries"][0]["path"]
    snapshot_paths = [f for f in (bundle / "concepts").glob("*.md")]
    assert snapshot_paths
    # Reading raw bytes goes through the pinned revision tree; remove it all.
    revisions = bundle / ".oknoll" / "revisions"
    if revisions.is_dir():
        shutil.rmtree(revisions)
    shutil.rmtree(bundle / "concepts")
    with pytest.raises(ToolkitError) as excinfo:
        toolkit.read(victim)
    message = str(excinfo.value)
    assert "cannot serve this request" in message or message.startswith("read:")
    # Whatever detail hit the operator log, the client message stays path-free.
    assert str(bundle) not in message


def test_cache_dir_index_is_built_and_reused(bundle: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "index"
    toolkit = BundleToolkit(bundle, index_cache=cache)
    toolkit.search("incident")
    built = list(cache.iterdir())
    assert len(built) == 1  # one content-keyed index directory
    stamp = built[0].stat().st_mtime_ns

    # A second toolkit over the same content finds the same index untouched.
    again = BundleToolkit(bundle, index_cache=cache)
    again.search("incident")
    assert [d.name for d in cache.iterdir()] == [built[0].name]
    assert built[0].stat().st_mtime_ns == stamp

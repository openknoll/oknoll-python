"""Phase 16 exit gate at the CLI boundary, fully offline.

Two bundles installed — one golden fixture, one foreign upstream bundle —
appear in the catalog, and `query ask local:<alias>` answers with citations
against the immutable store tree using the deterministic stub provider. Plus
the surrounding store surface: checkout round trip, unpack, image
build/tag/save/load/remove, and the malicious-archive rejection path.
"""

import contextlib
import json
from pathlib import Path

import pytest
from okf_core.revision import compare_trees, compute_revision_id
from oknoll_cli.main import app
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_MULTIHOP = REPO_ROOT / "fixtures" / "bundles" / "golden" / "multihop"
GOLDEN_MINIMAL = REPO_ROOT / "fixtures" / "bundles" / "golden" / "minimal"
FOREIGN = REPO_ROOT / "fixtures" / "bundles" / "upstream" / "acme_retail"
MALICIOUS = REPO_ROOT / "fixtures" / "security" / "archives" / "traversal.tar"

runner = CliRunner()


def _output(result: object) -> str:
    text = getattr(result, "output", "")
    with contextlib.suppress(AttributeError, ValueError):
        text += result.stderr  # type: ignore[attr-defined]
    return text


def _install(source: Path, alias: str) -> None:
    result = runner.invoke(app, ["bundle", "install", str(source), "--name", alias])
    assert result.exit_code == 0, _output(result)


def test_exit_gate_two_bundles_catalog_and_cited_local_ask() -> None:
    _install(GOLDEN_MULTIHOP, "handbook")
    _install(FOREIGN, "acme")

    listing = runner.invoke(app, ["bundle", "list"])
    assert listing.exit_code == 0, _output(listing)
    assert "handbook" in listing.output and "acme" in listing.output

    ask = runner.invoke(
        app, ["query", "ask", "local:handbook", "Who must sign off a production release?"]
    )
    assert ask.exit_code == 0, _output(ask)
    assert "Citations:" in ask.output
    assert "handbook@sha256:" in ask.output  # answer names the pinned identity

    ask_json = runner.invoke(
        app,
        [
            "query",
            "ask",
            "local:handbook",
            "Who must sign off a production release?",
            "--json",
        ],
    )
    assert ask_json.exit_code == 0, _output(ask_json)
    payload = json.loads(ask_json.output)
    assert payload["citations"], "cited answer required"
    assert payload["bundle"].startswith("handbook@sha256:")

    # Bare aliases stay an interactive convenience.
    bare = runner.invoke(app, ["query", "ask", "handbook", "Who must sign off a release?"])
    assert bare.exit_code == 0, _output(bare)

    # The foreign bundle is queryable too.
    foreign_ask = runner.invoke(app, ["query", "ask", "local:acme", "What is the return policy?"])
    assert foreign_ask.exit_code == 0, _output(foreign_ask)


def test_install_checkout_round_trip_preserves_bytes(tmp_path: Path) -> None:
    _install(GOLDEN_MINIMAL, "minimal")
    dest = tmp_path / "co"
    result = runner.invoke(app, ["bundle", "checkout", "local:minimal", str(dest)])
    assert result.exit_code == 0, _output(result)
    assert compare_trees(GOLDEN_MINIMAL, dest) == []
    assert compute_revision_id(dest) == compute_revision_id(GOLDEN_MINIMAL)


def test_inspect_and_uninstall() -> None:
    _install(GOLDEN_MINIMAL, "minimal")
    inspect = runner.invoke(app, ["bundle", "inspect", "minimal", "--json"])
    assert inspect.exit_code == 0, _output(inspect)
    entry = json.loads(inspect.output)
    assert entry["alias"] == "minimal"
    assert entry["oci_digest"].startswith("sha256:")
    assert entry["okf_revision"].startswith("rev-")

    assert runner.invoke(app, ["bundle", "uninstall", "minimal"]).exit_code == 0
    gone = runner.invoke(app, ["bundle", "inspect", "minimal"])
    assert gone.exit_code == 1
    assert "no installed bundle" in _output(gone)


def test_install_existing_alias_requires_update_flag() -> None:
    _install(GOLDEN_MINIMAL, "minimal")
    again = runner.invoke(app, ["bundle", "install", str(GOLDEN_MINIMAL), "--name", "minimal"])
    assert again.exit_code == 1
    assert "--update" in _output(again)
    updated = runner.invoke(
        app, ["bundle", "install", str(GOLDEN_MINIMAL), "--name", "minimal", "--update"]
    )
    assert updated.exit_code == 0, _output(updated)


def test_unpack_and_malicious_archive_rejection(tmp_path: Path) -> None:
    pack = runner.invoke(app, ["image", "build", str(GOLDEN_MINIMAL)])
    assert pack.exit_code == 0, _output(pack)

    bad = runner.invoke(app, ["bundle", "unpack", str(MALICIOUS), str(tmp_path / "out")])
    assert bad.exit_code == 1
    assert "traverses" in _output(bad)
    assert not (tmp_path / "out").exists()


def test_image_lifecycle_build_tag_save_load_remove(tmp_path: Path) -> None:
    build = runner.invoke(app, ["image", "build", str(GOLDEN_MINIMAL), "--tag", "minimal:1.0"])
    assert build.exit_code == 0, _output(build)
    digest = next(
        line.split()[1] for line in build.output.splitlines() if line.startswith("built ")
    )

    listing = runner.invoke(app, ["image", "list", "--json"])
    rows = json.loads(listing.output)
    assert [row["digest"] for row in rows] == [digest]
    assert rows[0]["tags"] == ["minimal:1.0"]

    inspect = runner.invoke(app, ["image", "inspect", "minimal:1.0"])
    assert inspect.exit_code == 0, _output(inspect)
    payload = json.loads(inspect.output)
    assert payload["digest"] == digest
    assert payload["config"]["bundleRevision"].startswith("rev-")

    tagged = runner.invoke(app, ["image", "tag", "minimal:1.0", "minimal:stable"])
    assert tagged.exit_code == 0, _output(tagged)

    out_tar = tmp_path / "minimal.tar"
    save = runner.invoke(app, ["image", "save", "minimal:1.0", "-o", str(out_tar)])
    assert save.exit_code == 0, _output(save)
    assert out_tar.is_file()

    removed = runner.invoke(app, ["image", "remove", "minimal:stable"])
    assert removed.exit_code == 0, _output(removed)
    assert json.loads(runner.invoke(app, ["image", "list", "--json"]).output) == []

    load = runner.invoke(app, ["image", "load", str(out_tar)])
    assert load.exit_code == 0, _output(load)
    assert digest in load.output


def test_image_remove_refuses_installed_digest() -> None:
    _install(GOLDEN_MINIMAL, "minimal")
    entry = json.loads(runner.invoke(app, ["bundle", "inspect", "minimal", "--json"]).output)
    result = runner.invoke(app, ["image", "remove", entry["oci_digest"]])
    assert result.exit_code == 1
    assert "uninstall" in _output(result)


@pytest.mark.parametrize("target", ["local:nope", "nope"])
def test_ask_unknown_alias_fails_cleanly(target: str) -> None:
    result = runner.invoke(app, ["query", "ask", target, "anything?"])
    assert result.exit_code == 1
    assert "no installed bundle" in _output(result)

"""Permissive-consumer regression over the vendored upstream OKF reference bundles.

Foreign bundles must never produce build-failing errors — unknown types, keys,
and hygiene issues degrade into warnings/info (ADR-0003). Their frontmatter must
also survive a parse → write → parse round trip with no field loss.
"""

import shutil
from pathlib import Path

import pytest
from okf_core import (
    Explorer,
    StubModelProvider,
    answer_question,
    lint_bundle,
    parse_document,
    write_document,
)

UPSTREAM = Path(__file__).resolve().parents[3] / "fixtures" / "bundles" / "upstream"
BUNDLES = sorted(p for p in UPSTREAM.iterdir() if p.is_dir())


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda p: p.name)
def test_upstream_bundle_lints_without_errors(bundle: Path) -> None:
    report = lint_bundle(bundle)
    errors = [f for f in report.sorted_findings() if f.severity.value == "error"]
    assert not errors, [f.to_dict() for f in errors]


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda p: p.name)
def test_upstream_frontmatter_round_trips(bundle: Path) -> None:
    for md in sorted(bundle.rglob("*.md")):
        original = parse_document(md.read_text(encoding="utf-8"))
        rewritten = parse_document(write_document(original))
        if original.frontmatter is None:
            assert rewritten.frontmatter is None, md
        else:
            assert rewritten.frontmatter is not None, md
            assert rewritten.frontmatter.data == original.frontmatter.data, md


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda p: p.name)
def test_upstream_bundle_is_explorable(bundle: Path, tmp_path: Path) -> None:
    """Someone else's bundle must be navigable, unknown types and all.

    Copied first because exploring builds a derived index — a foreign bundle is
    read-only as far as its content is concerned, but the index has to land
    somewhere.
    """
    dest = tmp_path / bundle.name
    shutil.copytree(bundle, dest)
    explorer = Explorer(dest, today="2026-08-05")

    overview = explorer.overview()
    assert overview["counts"]["concepts"] > 0
    assert overview["types"], "upstream bundles declare types we do not define ourselves"

    listing = explorer.list(".", limit=5)
    assert listing["entries"]
    first = str(listing["entries"][0]["path"])

    assert explorer.peek(first)["path"] == first
    assert explorer.read(first)["body"]
    assert set(explorer.links(first)) == {
        "path",
        "outbound",
        "inbound",
        "outbound_total",
        "inbound_total",
    }
    assert explorer.history()["entries"] is not None
    assert explorer.search("table dataset schema")["results"] is not None


@pytest.mark.parametrize("bundle", BUNDLES, ids=lambda p: p.name)
def test_ask_over_an_upstream_bundle_never_crashes(bundle: Path, tmp_path: Path) -> None:
    dest = tmp_path / bundle.name
    shutil.copytree(bundle, dest)
    result = answer_question(
        bundle_dir=dest,
        question="What is the main topic covered here?",
        provider=StubModelProvider(),
        today="2026-08-05",
    )
    # Either it finds grounded evidence or it abstains — never invents.
    assert result.abstained == (not result.citations)
    assert result.trace["revision_id"] is None  # a foreign bundle has no local revision

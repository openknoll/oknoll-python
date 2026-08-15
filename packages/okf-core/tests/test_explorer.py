"""The seven deterministic explorer tools and their guardrails.

Every tool is exercised against frozen golden bundles, so both the shape of the
returned payloads and the determinism of ranked results are pinned here — this
is the contract the CLI, the web app, and MCP all reuse verbatim.
"""

import json
import shutil
from pathlib import Path

import pytest
from okf_core import explorer as explorer_mod
from okf_core import indexing
from okf_core.explorer import TOOLS, Explorer, ExplorerError
from okf_core.revision import build_manifest, compute_revision_id, publish_revision

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "fixtures" / "bundles" / "golden"
MINIMAL = GOLDEN / "minimal"
MULTIHOP = GOLDEN / "multihop"


@pytest.fixture()
def minimal(tmp_path: Path) -> Path:
    """A writable copy: ensure_index writes derived state under .oknoll/."""
    dest = tmp_path / "minimal"
    shutil.copytree(MINIMAL, dest)
    return dest


@pytest.fixture()
def multihop(tmp_path: Path) -> Path:
    dest = tmp_path / "multihop"
    shutil.copytree(MULTIHOP, dest)
    return dest


def test_tool_surface_is_the_seven_from_the_design(minimal: Path) -> None:
    assert TOOLS == ("overview", "list", "search", "peek", "read", "links", "history")
    explorer = Explorer(minimal)
    for tool in TOOLS:
        assert callable(getattr(explorer, tool)), tool


def test_overview_summarizes_types_tags_and_trust(minimal: Path) -> None:
    overview = Explorer(minimal, today="2026-08-04").overview()
    assert overview["title"] == "Minimal golden bundle"
    assert overview["counts"] == {"concepts": 2, "references": 1}
    assert overview["types"] == {"Concept": 2}
    assert overview["tags"] == {"architecture": 2, "security": 1}
    assert overview["trust"] == {
        "statuses": {"draft": 1, "stable": 1},
        "verified": 1,
        "unverified": 1,
    }
    assert overview["freshness"] == {"stale": 0}


def test_tools_share_one_file_snapshot_across_a_mid_session_rebuild(minimal: Path) -> None:
    """A file appearing mid-session must not desync overview/list from _parse.

    The session binds the file set at first use; a later on-disk change (a
    rebuild) is simply not seen — every tool stays consistent instead of some
    tools noticing the new file and others hard-failing to look it up.
    """
    explorer = Explorer(minimal)
    assert explorer.overview()["counts"]["concepts"] == 2

    (minimal / "concepts" / "added-after-startup.md").write_text(
        "---\ntitle: Late\ntype: Concept\n---\n\nAppeared after the snapshot.\n",
        encoding="utf-8",
    )
    # overview and list must not blow up, and must agree with the pinned set.
    assert explorer.overview()["counts"]["concepts"] == 2
    listing = explorer.list("concepts")
    assert listing["total"] == 2
    assert all(e["path"] != "concepts/added-after-startup.md" for e in listing["entries"])


def test_list_is_metadata_only_and_filters(minimal: Path) -> None:
    explorer = Explorer(minimal)
    listing = explorer.list("concepts")
    assert listing["total"] == 2
    entries = listing["entries"]
    assert [e["path"] for e in entries] == [
        "concepts/architecture.md",
        "concepts/authentication.md",
    ]
    assert all("body" not in e for e in entries)  # metadata only (explorer guardrail)

    assert Explorer(minimal).list("concepts", status="stable")["total"] == 1
    assert Explorer(minimal).list("concepts", tag="security")["total"] == 1
    assert Explorer(minimal).list("concepts", type="Nonexistent")["total"] == 0


def test_list_bounds_the_count(minimal: Path) -> None:
    listing = Explorer(minimal).list("concepts", limit=1)
    assert listing["total"] == 2  # total is honest…
    assert len(listing["entries"]) == 1  # …but the payload is bounded


def test_list_accepts_the_bundle_root(minimal: Path) -> None:
    """ "." and "/" mean the root — not hidden state to be refused."""
    for directory in (".", "/", ""):
        listing = Explorer(minimal).list(directory)
        assert listing["directory"] == "."
        assert listing["total"] == 3  # 2 concepts + 1 reference


@pytest.mark.parametrize("directory", ["..", "../..", ".oknoll", "concepts/../.oknoll"])
def test_list_refuses_to_leave_the_bundle(minimal: Path, directory: str) -> None:
    with pytest.raises(ExplorerError, match="escapes the bundle root"):
        Explorer(minimal).list(directory)


def test_search_is_ranked_deterministic_and_snippet_only(minimal: Path) -> None:
    first = Explorer(minimal).search("authentication session boundary")
    second = Explorer(minimal).search("authentication session boundary")
    assert first == second  # same bundle + same query → same ranking, always
    results = first["results"]
    assert results, "expected lexical hits"
    assert all(set(r) == {"path", "kind", "title", "snippet", "score"} for r in results)
    assert all(len(str(r["snippet"])) < 400 for r in results)  # snippets, never bodies


def test_search_rebuilt_index_ranks_identically(minimal: Path) -> None:
    before = Explorer(minimal).search("architecture")
    shutil.rmtree(minimal / indexing.INDEX_DIR)
    assert Explorer(minimal).search("architecture") == before


def test_peek_is_cheaper_than_read(minimal: Path) -> None:
    explorer = Explorer(minimal)
    peeked = explorer.peek("concepts/architecture.md", lines=2)
    read = explorer.read("concepts/architecture.md")
    assert peeked["frontmatter"]["title"] == "System architecture"
    assert len(str(peeked["body_start"])) < len(str(read["body"]))
    assert peeked["truncated"] is True


def test_peek_caps_chars_not_just_lines(minimal: Path) -> None:
    """One enormous line must not make "cheap triage" cost more than a read."""
    hostile = minimal / "concepts" / "architecture.md"
    text = hostile.read_text(encoding="utf-8")
    hostile.write_text(text + "\n" + "A" * 100_000 + "\n", encoding="utf-8")
    peeked = Explorer(minimal).peek("concepts/architecture.md", lines=100)
    assert len(str(peeked["body_start"])) <= explorer_mod.MAX_PEEK_CHARS
    assert peeked["truncated"] is True


def test_peek_and_read_bound_oversized_frontmatter(minimal: Path) -> None:
    """A huge frontmatter value must not defeat the body cap (bounded outputs)."""
    target = minimal / "concepts" / "architecture.md"
    body = target.read_text(encoding="utf-8").split("---\n", 2)[2]
    target.write_text(
        "---\ntitle: Big\ntype: Concept\nnote: " + "X" * 300_000 + "\n---\n" + body,
        encoding="utf-8",
    )
    explorer = Explorer(minimal)
    for tool in (explorer.peek, explorer.read):
        frontmatter = tool("concepts/architecture.md")["frontmatter"]
        serialized = json.dumps(frontmatter, default=str)
        assert len(serialized) <= explorer_mod.MAX_FRONTMATTER_CHARS + 200
        assert frontmatter["_truncated"] is True


def test_read_returns_body_links_and_freshness(minimal: Path) -> None:
    read = Explorer(minimal, today="2026-08-04").read("concepts/architecture.md")
    assert "modular monolith" in str(read["body"])
    assert read["truncated"] is False
    assert read["stale"] is False
    resolved = {link["resolved"] for link in read["links"]}
    assert "concepts/authentication.md" in resolved


def test_read_caps_size(minimal: Path) -> None:
    read = Explorer(minimal).read("concepts/architecture.md", max_chars=20)
    assert len(str(read["body"])) == 20
    assert read["truncated"] is True


def test_links_reports_both_directions_bounded(minimal: Path) -> None:
    edges = Explorer(minimal).links("concepts/authentication.md")
    assert "index.md" in edges["inbound"]
    assert "concepts/architecture.md" in edges["inbound"]
    outbound = Explorer(minimal).links("concepts/architecture.md", direction="outbound")
    assert outbound["inbound"] == []
    assert "concepts/authentication.md" in outbound["outbound"]

    bounded = Explorer(minimal).links("concepts/authentication.md", limit=1)
    assert len(bounded["inbound"]) == 1
    assert bounded["inbound_total"] >= 2  # the total stays honest


def test_links_rejects_a_bad_direction(minimal: Path) -> None:
    with pytest.raises(ExplorerError, match="direction"):
        Explorer(minimal).links("concepts/architecture.md", direction="sideways")


def test_history_reads_the_log_newest_first(multihop: Path) -> None:
    history = Explorer(multihop).history()
    assert history["entries"] == []  # fixture log has no rev- entries
    (multihop / "log.md").write_text(
        "# Revision log\n\n- rev-000000000001 — first\n- rev-000000000002 — second\n",
        encoding="utf-8",
    )
    entries = Explorer(multihop).history()["entries"]
    assert [e["revision_id"] for e in entries] == ["rev-000000000002", "rev-000000000001"]


@pytest.mark.parametrize(
    "path",
    [
        "../../../etc/passwd",
        "/../../etc/passwd",
        "concepts/../../secrets.md",
        "/.oknoll/current",
        ".oknoll/index/graph.json",
        "",
    ],
)
def test_read_refuses_paths_outside_the_bundle(minimal: Path, path: str) -> None:
    with pytest.raises(ExplorerError):
        Explorer(minimal).read(path)


def test_read_refuses_non_markdown_and_missing_files(minimal: Path) -> None:
    with pytest.raises(ExplorerError, match="no such bundle file"):
        Explorer(minimal).read("concepts/does-not-exist.md")
    with pytest.raises(ExplorerError, match="not a markdown file"):
        Explorer(minimal).read("manifest.json")


def test_paths_must_match_the_bundle_exactly(minimal: Path) -> None:
    """On a case-insensitive filesystem this would otherwise open the file but
    miss every index and graph lookup, silently reporting no links."""
    with pytest.raises(ExplorerError, match="no such bundle file"):
        Explorer(minimal).read("CONCEPTS/ARCHITECTURE.md")


def test_symlinks_out_of_the_bundle_are_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("---\ntitle: S\n---\n\ntop secret\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "index.md").write_text("---\ntitle: B\n---\n\n# B\n", encoding="utf-8")
    (bundle / "concepts" / "leak.md").symlink_to(outside / "secret.md")
    (bundle / "escape").symlink_to(outside)

    explorer = Explorer(bundle)
    with pytest.raises(ExplorerError):
        explorer.read("concepts/leak.md")
    with pytest.raises(ExplorerError):
        explorer.read("escape/secret.md")
    # Nor does the walk descend into a symlinked directory and index what's there.
    assert explorer.search("top secret")["results"] == []


def test_a_symlink_into_derived_state_is_not_bundle_content(tmp_path: Path) -> None:
    """A link wearing a concept's name but pointing at .oknoll/ would otherwise
    serve prior traces as evidence — and make the revision id depend on them."""
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / ".oknoll" / "traces").mkdir(parents=True)
    trace = bundle / ".oknoll" / "traces" / "t.json"
    trace.write_text('{"evidence": "PRIORQUESTION alpha"}', encoding="utf-8")
    (bundle / "index.md").write_text("---\ntitle: B\n---\n\n# B\n", encoding="utf-8")
    (bundle / "concepts" / "real.md").write_text(
        "---\ntype: Concept\ntitle: R\n---\n\n# R\n\nreal content\n", encoding="utf-8"
    )
    (bundle / "concepts" / "leak.md").symlink_to("../.oknoll/traces/t.json")

    explorer = Explorer(bundle)
    with pytest.raises(ExplorerError):
        explorer.read("concepts/leak.md")
    assert explorer.search("PRIORQUESTION alpha")["results"] == []

    before = compute_revision_id(bundle)
    trace.write_text('{"evidence": "DIFFERENT beta"}', encoding="utf-8")
    assert compute_revision_id(bundle) == before  # derived state cannot move a revision
    manifest_files = build_manifest(bundle)["files"]
    assert isinstance(manifest_files, dict)
    assert "concepts/leak.md" not in manifest_files  # nor be packed


def test_non_string_frontmatter_keys_stay_json_safe(tmp_path: Path) -> None:
    """YAML allows non-string keys; every tool promises a JSON-safe dict."""
    bundle = tmp_path / "bundle"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "index.md").write_text(
        "---\ntitle: B\n---\n\n# B\n\n- [E](/concepts/odd.md)\n", encoding="utf-8"
    )
    (bundle / "concepts" / "odd.md").write_text(
        "---\ntitle: Odd\n? !!binary aGk=\n: x\n---\n\nsome body text\n", encoding="utf-8"
    )
    for payload in (
        Explorer(bundle).read("concepts/odd.md"),
        Explorer(bundle).peek("concepts/odd.md"),
    ):
        assert all(isinstance(key, str) for key in payload["frontmatter"])
        json.dumps(payload)  # must not raise


def test_read_reports_links_past_the_size_cap(minimal: Path) -> None:
    """The cap bounds prose; it must not silently drop links `links` still reports."""
    clipped = Explorer(minimal).read("concepts/architecture.md", max_chars=10)
    assert clipped["truncated"] is True
    assert len(str(clipped["body"])) == 10
    resolved = {link["resolved"] for link in clipped["links"]}
    assert "concepts/authentication.md" in resolved


def test_root_absolute_and_relative_paths_are_equivalent(minimal: Path) -> None:
    explorer = Explorer(minimal)
    assert explorer.read("/concepts/architecture.md") == explorer.read("concepts/architecture.md")


def test_missing_bundle_directory_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ExplorerError, match="bundle directory not found"):
        Explorer(tmp_path / "nope")


def test_stale_after_drives_freshness(multihop: Path) -> None:
    concept = multihop / "concepts" / "duty-roster.md"
    concept.write_text(
        concept.read_text(encoding="utf-8").replace(
            "status: draft", "status: draft\nstale_after: 2026-01-01"
        ),
        encoding="utf-8",
    )
    assert Explorer(multihop, today="2026-08-04").overview()["freshness"] == {"stale": 1}
    assert Explorer(multihop, today="2025-06-01").overview()["freshness"] == {"stale": 0}


def test_pinned_session_survives_a_mid_session_rebuild(minimal: Path, tmp_path: Path) -> None:
    """With a revision store the session reads the immutable revision.

    A rebuild that publishes a new revision and rematerializes the top level
    changes nothing the running session returns — bytes, search results, or
    revision id. A fresh session pins the new revision.
    """
    bundle = tmp_path / "published"
    bundle.mkdir()
    rev_a = compute_revision_id(minimal)
    publish_revision(bundle, minimal, rev_a)

    explorer = Explorer(bundle)
    overview = explorer.overview()
    assert overview["revision_id"] == rev_a
    assert overview["revision_pinned"] is True
    body_before = explorer.read("concepts/authentication.md")["body"]

    staged_b = tmp_path / "staged-b"
    shutil.copytree(minimal, staged_b)
    concept = staged_b / "concepts" / "authentication.md"
    concept.write_text(
        concept.read_text(encoding="utf-8") + "\nA quixotic amendment.\n", encoding="utf-8"
    )
    rev_b = compute_revision_id(staged_b)
    assert rev_b != rev_a
    publish_revision(bundle, staged_b, rev_b)

    # the running session is unmoved: same bytes, same revision, no new term
    assert explorer.read("concepts/authentication.md")["body"] == body_before
    assert explorer.overview()["revision_id"] == rev_a
    assert explorer.search("quixotic")["results"] == []
    assert explorer.history()["current_revision_id"] == rev_a

    # a fresh session pins the new revision and sees the change
    fresh = Explorer(bundle)
    assert fresh.overview()["revision_id"] == rev_b
    assert "quixotic amendment" in fresh.read("concepts/authentication.md")["body"]
    assert [hit["path"] for hit in fresh.search("quixotic")["results"]] == [
        "concepts/authentication.md"
    ]

    # indexing a pinned tree never writes derived state into the revision dir
    assert not (bundle / ".oknoll" / "revisions" / rev_a / ".oknoll").exists()
    assert not (bundle / ".oknoll" / "revisions" / rev_b / ".oknoll").exists()


def test_bundle_without_a_revision_store_is_not_pinned(minimal: Path) -> None:
    """Foreign bundles (no .oknoll/ store) fall back to the materialized tree."""
    overview = Explorer(minimal).overview()
    assert overview["revision_pinned"] is False
    assert overview["revision_id"] is None

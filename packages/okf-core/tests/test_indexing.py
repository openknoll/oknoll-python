"""Derived indexes: link graph + SQLite FTS, built at pipeline index time."""

import json
import shutil
import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest
from okf_core import indexing
from okf_core.explorer import Explorer
from okf_core.revision import compute_revision_id

REPO_ROOT = Path(__file__).resolve().parents[3]
MINIMAL = REPO_ROOT / "fixtures" / "bundles" / "golden" / "minimal"


@pytest.fixture()
def minimal(tmp_path: Path) -> Path:
    dest = tmp_path / "minimal"
    shutil.copytree(MINIMAL, dest)
    return dest


def test_link_graph_is_sorted_and_bidirectional(minimal: Path) -> None:
    graph = indexing.build_link_graph(minimal)
    assert graph["outbound"]["index.md"] == [
        "concepts/architecture.md",
        "concepts/authentication.md",
    ]
    assert "index.md" in graph["inbound"]["concepts/architecture.md"]
    assert list(graph["outbound"]) == sorted(graph["outbound"])
    assert all(v == sorted(v) for v in graph["outbound"].values())


def test_index_is_keyed_by_revision_id_and_reused(minimal: Path) -> None:
    revision_id = compute_revision_id(minimal)
    built = indexing.ensure_index(minimal)
    assert built == indexing.index_dir_for(minimal, revision_id)
    assert (built / indexing.FTS_NAME).is_file()

    stamp = (built / indexing.FTS_NAME).stat().st_mtime_ns
    assert indexing.ensure_index(minimal) == built
    assert (built / indexing.FTS_NAME).stat().st_mtime_ns == stamp  # reused, not rebuilt


def test_changed_content_gets_a_new_index_directory(minimal: Path) -> None:
    first = indexing.ensure_index(minimal)
    (minimal / "concepts" / "architecture.md").write_text(
        "---\ntype: Reference\ntitle: Rewritten\n---\n\n# Rewritten\n\nDifferent words.\n",
        encoding="utf-8",
    )
    second = indexing.ensure_index(minimal)
    assert second != first
    assert first.is_dir()  # old index is simply unused, never mutated


def test_search_ranks_deterministically_across_rebuilds(minimal: Path) -> None:
    first = indexing.search_index(indexing.ensure_index(minimal), "authentication boundary")
    shutil.rmtree(minimal / indexing.INDEX_DIR)
    second = indexing.search_index(indexing.ensure_index(minimal), "authentication boundary")
    assert [h.to_dict() for h in first] == [h.to_dict() for h in second]


def test_search_bounds_results(minimal: Path) -> None:
    hits = indexing.search_index(indexing.ensure_index(minimal), "architecture", limit=1)
    assert len(hits) == 1


def test_query_drops_stopwords_and_quotes_terms() -> None:
    assert indexing.question_terms("What is the release process?") == ["release", "process"]
    assert indexing.fts_query("How are credentials stored?") == '"credentials" OR "stored"'
    assert indexing.fts_query("who is it for") == ""  # all stopwords


def test_non_ascii_questions_are_not_silently_dropped(tmp_path: Path) -> None:
    """An ASCII-only tokenizer would turn these into empty queries and find nothing."""
    bundle = tmp_path / "unicode"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "index.md").write_text(
        "---\ntitle: Ünicode\n---\n\n# Ü\n\n- [Café](/concepts/cafe.md)\n", encoding="utf-8"
    )
    (bundle / "concepts" / "cafe.md").write_text(
        "---\ntype: Reference\ntitle: Café\n---\n\n"
        "# Café\n\nEspresso naïve résumé — 日本語 テスト.\n",
        encoding="utf-8",
    )
    index = indexing.ensure_index(bundle)
    assert indexing.question_terms("Où est le café?") == ["où", "est", "le", "café"]
    for query in ("café", "naïve résumé", "日本語"):
        assert [h.path for h in indexing.search_index(index, query)][:1] == ["concepts/cafe.md"], (
            query
        )


def test_fts_special_characters_cannot_break_the_query(minimal: Path) -> None:
    index = indexing.ensure_index(minimal)
    # FTS5 syntax in the question is neutralized by term quoting, not executed.
    assert indexing.search_index(index, 'architecture" OR docs MATCH "') is not None
    assert indexing.search_index(index, "NEAR(a b) AND *") == []


def test_read_only_bundle_is_indexed_out_of_tree(minimal: Path) -> None:
    """Exploring someone else's bundle must not require writing into it."""
    minimal.chmod(0o500)
    try:
        index = indexing.ensure_index(minimal)
        assert not index.is_relative_to(minimal)
        assert indexing.search_index(index, "architecture")
    finally:
        minimal.chmod(0o700)


def test_read_only_fallback_does_not_use_a_predictable_shared_path(minimal: Path) -> None:
    """The revision id is content-derived, so anyone with the same bundle could
    predict a path built from it and pre-seed a poisoned index on a shared /tmp."""
    revision_id = compute_revision_id(minimal)
    poison = Path(tempfile.gettempdir()) / "oknoll-index" / revision_id
    shutil.rmtree(poison, ignore_errors=True)
    poison.mkdir(parents=True)
    (poison / indexing.GRAPH_NAME).write_text(
        json.dumps({"outbound": {"concepts/architecture.md": ["concepts/attacker.md"]}}),
        encoding="utf-8",
    )
    con = sqlite3.connect(poison / indexing.FTS_NAME)
    con.execute(
        "CREATE VIRTUAL TABLE docs USING fts5(path UNINDEXED, kind UNINDEXED, "
        "title, description, tags, body, tokenize='porter unicode61')"
    )
    con.execute(
        "INSERT INTO docs VALUES (?,?,?,?,?,?)",
        ("concepts/attacker.md", "concept", "Pwned", "", "", "ATTACKER SNIPPET architecture"),
    )
    con.commit()
    con.close()

    minimal.chmod(0o500)
    try:
        explorer = Explorer(minimal)
        hits = [h["path"] for h in explorer.search("architecture")["results"]]
        assert "concepts/attacker.md" not in hits
        assert explorer.links("concepts/architecture.md")["outbound"] == [
            "concepts/authentication.md"
        ]
    finally:
        minimal.chmod(0o700)
        shutil.rmtree(poison, ignore_errors=True)


def test_concurrent_index_builds_agree_and_never_crash(tmp_path: Path) -> None:
    """A half-built index would not just crash a reader — it would rank differently."""
    bundle = tmp_path / "big"
    (bundle / "concepts").mkdir(parents=True)
    (bundle / "index.md").write_text("---\ntitle: Big\n---\n\n# Big\n", encoding="utf-8")
    for i in range(300):
        (bundle / "concepts" / f"c{i}.md").write_text(
            f"---\ntype: Reference\ntitle: C{i}\n---\n\n# C{i}\n\nrelease process notes {i}\n",
            encoding="utf-8",
        )

    errors: list[str] = []
    results: list[str] = []

    def explore() -> None:
        try:
            results.append(json.dumps(Explorer(bundle).search("release process")))
        except Exception as exc:  # any failure at all is the bug under test
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=explore) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(set(results)) == 1, "concurrent readers saw different rankings"


def test_log_is_excluded_from_search(minimal: Path) -> None:
    index = indexing.ensure_index(minimal)
    hits = indexing.search_index(index, "revision log fixture")
    assert all(h.path != "log.md" for h in hits)

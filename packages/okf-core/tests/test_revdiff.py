"""Semantic revision diff: role classification, source re-pins, link edges.

Two revisions are published into one bundle from staged variants of the
minimal golden fixture; the report must classify every change by bundle role
and never surface bookkeeping files (log.md, manifest.json).
"""

import shutil
from pathlib import Path

import pytest
from okf_core.revdiff import RevisionDiffError, available_revisions, diff_revisions
from okf_core.revision import compute_revision_id, publish_revision

REPO_ROOT = Path(__file__).resolve().parents[3]
MINIMAL = REPO_ROOT / "fixtures" / "bundles" / "golden" / "minimal"

_PIN = """---
title: policy.pdf
openknoll_source:
  connector: files
  uri: sources/policy.pdf
  source_hash: {digest}
---

Normalized snapshot body.
"""


@pytest.fixture()
def published(tmp_path: Path) -> tuple[Path, str, str]:
    """One bundle with two revisions: a retitle, a re-pin, an add, a removal."""
    staged_a = tmp_path / "staged-a"
    shutil.copytree(MINIMAL, staged_a)
    (staged_a / "references" / "source-002.md").write_text(
        _PIN.format(digest="sha256:aaa"), encoding="utf-8"
    )
    (staged_a / "references" / "source-009.md").write_text("# doomed\n", encoding="utf-8")

    staged_b = tmp_path / "staged-b"
    shutil.copytree(staged_a, staged_b)
    concept = staged_b / "concepts" / "authentication.md"
    concept.write_text(
        concept.read_text(encoding="utf-8").replace(
            "title: Authentication architecture", "title: Authentication overhaul"
        ),
        encoding="utf-8",
    )
    (staged_b / "references" / "source-002.md").write_text(
        _PIN.format(digest="sha256:bbb"), encoding="utf-8"
    )
    (staged_b / "concepts" / "rotation.md").write_text(
        "---\ntitle: Key rotation\n---\n\nSee [architecture](architecture.md).\n",
        encoding="utf-8",
    )
    (staged_b / "references" / "source-009.md").unlink()
    # bookkeeping churn that must never surface in a report
    (staged_b / "log.md").write_text("# Revision log\n\n- rev-bbbbbbbbbbbb — b\n", encoding="utf-8")

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    rev_a = compute_revision_id(staged_a)
    publish_revision(bundle, staged_a, rev_a)
    rev_b = compute_revision_id(staged_b)
    publish_revision(bundle, staged_b, rev_b)
    assert rev_a != rev_b
    return bundle, rev_a, rev_b


def test_diff_classifies_changes_by_bundle_role(published: tuple[Path, str, str]) -> None:
    bundle, rev_a, rev_b = published
    report = diff_revisions(bundle, rev_a, rev_b)

    assert report["rev_a"] == rev_a
    assert report["rev_b"] == rev_b
    assert report["files"] == {
        "added": ["concepts/rotation.md"],
        "removed": ["references/source-009.md"],
        "changed": ["concepts/authentication.md", "references/source-002.md"],
    }
    assert report["concepts"] == {
        "added": [{"path": "concepts/rotation.md", "title": "Key rotation"}],
        "removed": [],
        "changed": [
            {
                "path": "concepts/authentication.md",
                "retitled": True,
                "title_a": "Authentication architecture",
                "title_b": "Authentication overhaul",
            }
        ],
    }
    assert report["references"] == {
        "added": [],
        # no frontmatter on the doomed file — the title falls back to the path
        "removed": [{"path": "references/source-009.md", "title": "references/source-009.md"}],
        "changed": [
            {
                "path": "references/source-002.md",
                "repinned": True,
                "uri_changed": False,
                "source_hash_changed": True,
            }
        ],
    }
    assert report["links"] == {
        "added": [["concepts/rotation.md", "concepts/architecture.md"]],
        "removed": [],
    }


def test_bookkeeping_files_never_surface(published: tuple[Path, str, str]) -> None:
    bundle, rev_a, rev_b = published
    files = diff_revisions(bundle, rev_a, rev_b)["files"]
    everything = files["added"] + files["removed"] + files["changed"]
    assert "log.md" not in everything
    assert "manifest.json" not in everything


def test_same_revision_diffs_empty(published: tuple[Path, str, str]) -> None:
    bundle, rev_a, _ = published
    report = diff_revisions(bundle, rev_a, rev_a)
    assert report["files"] == {"added": [], "removed": [], "changed": []}
    assert report["links"] == {"added": [], "removed": []}
    assert report["concepts"] == {"added": [], "removed": [], "changed": []}
    assert report["references"] == {"added": [], "removed": [], "changed": []}


def test_unknown_revision_names_the_available_ones(published: tuple[Path, str, str]) -> None:
    bundle, rev_a, rev_b = published
    assert available_revisions(bundle) == sorted([rev_a, rev_b])
    with pytest.raises(RevisionDiffError, match="available"):
        diff_revisions(bundle, "rev-000000000000", rev_b)


def test_bundle_without_revisions_fails_clearly(tmp_path: Path) -> None:
    assert available_revisions(tmp_path) == []
    with pytest.raises(RevisionDiffError, match="none published"):
        diff_revisions(tmp_path, "rev-000000000000", "rev-000000000001")

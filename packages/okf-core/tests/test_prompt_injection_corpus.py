"""Prompt-injection release gate: bundle text is untrusted data (design §7.3).

Parametrized over ``fixtures/security/prompt-injection/cases.json``, exactly as
the SSRF corpus is. The invariant under test is narrow and absolute: nothing a
bundle *says* can change what the explorer *does*. Hostile text may be quoted
back as evidence; it may never resolve a path outside the bundle, reach the
network, alter a trust warning, or become policy.

A failure here stops a release.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from okf_core import StubModelProvider, answer_question
from okf_core.explorer import Explorer, ExplorerError

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS = REPO_ROOT / "fixtures" / "security" / "prompt-injection"
SPEC: dict[str, Any] = json.loads((CORPUS / "cases.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = SPEC["cases"]
QUESTION: str = SPEC["question"]


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    dest = tmp_path / "hostile"
    shutil.copytree(CORPUS / SPEC["bundle"], dest)
    return dest


def _case_ids() -> list[str]:
    return [case["id"] for case in CASES]


def test_corpus_is_non_trivial() -> None:
    categories = {case["category"] for case in CASES}
    assert categories >= {
        "instruction-override",
        "tool-coercion",
        "path-traversal",
        "exfiltration",
        "authority-spoof",
    }


@pytest.mark.parametrize("case", CASES, ids=_case_ids())
def test_injection_case(case: dict[str, Any], bundle: Path) -> None:
    explorer = Explorer(bundle, today="2026-08-05")
    expect = case["expect"]

    if expect == "quoted-as-data":
        # Present verbatim in the file and returned verbatim by read — the
        # explorer neither obeys it nor silently strips it.
        raw = (bundle / case["path"]).read_text(encoding="utf-8")
        assert case["needle"] in raw
        body = str(explorer.read(case["path"])["body"])
        assert case["needle"] in body

    elif expect == "unknown-key-preserved":
        # A frontmatter key that looks like policy is just an unknown key.
        frontmatter = explorer.read(case["path"])["frontmatter"]
        assert isinstance(frontmatter, dict)
        assert case["needle"] in frontmatter

    elif expect == "link-unresolved":
        links = explorer.read(case["path"])["links"]
        matches = [link for link in links if link["target"] == case["target"]]
        assert matches, f"corpus drift: {case['target']} is no longer in {case['path']}"
        for link in matches:
            assert link["resolved"] is None  # escapes the root → never resolved
        assert case["target"] not in explorer.links(case["path"])["outbound"]

    elif expect == "read-rejected":
        with pytest.raises(ExplorerError):
            explorer.read(case["target"])

    elif expect == "link-external-never-fetched":
        links = explorer.read(case["path"])["links"]
        matches = [link for link in links if link["target"] == case["target"]]
        assert matches, f"corpus drift: {case['target']} is no longer in {case['path']}"
        for link in matches:
            assert link["external"] is True
            assert link["resolved"] is None
        assert case["target"] not in explorer.links(case["path"])["outbound"]

    elif expect == "warnings-still-emitted":
        # The body claims administrator verification; the frontmatter says draft
        # and records no verification. The frontmatter wins.
        frontmatter = explorer.read(case["path"])["frontmatter"]
        assert isinstance(frontmatter, dict)
        assert frontmatter["status"] == "draft"
        assert "verified" not in frontmatter

    else:  # pragma: no cover — a new expect value needs a new assertion branch
        pytest.fail(f"unhandled expectation {expect!r} in case {case['id']!r}")


def test_ask_over_hostile_bundle_stays_inside_the_bundle(bundle: Path) -> None:
    result = answer_question(
        bundle_dir=bundle,
        question=QUESTION,
        provider=StubModelProvider(),
        today="2026-08-05",
    )
    inside = {f.relative_to(bundle).as_posix() for f in bundle.rglob("*.md")}
    assert set(result.trace["paths_read"]) <= inside
    assert {c.path for c in result.citations} <= inside
    assert result.trace["condition"] == "pd"


def test_injected_authority_never_suppresses_trust_warnings(bundle: Path) -> None:
    result = answer_question(
        bundle_dir=bundle,
        question=QUESTION,
        provider=StubModelProvider(),
        today="2026-08-05",
    )
    assert result.citations, "expected the hostile concepts to be cited as evidence"
    for citation in result.citations:
        assert any(citation.path in warning for warning in result.warnings), citation.path
        assert any(f"{citation.path}: status is draft" in warning for warning in result.warnings), (
            citation.path
        )


def test_okf_core_cannot_reach_the_network() -> None:
    """Structural guarantee: no explorer path can fetch a URL a bundle names."""
    import okf_core

    package_root = Path(okf_core.__file__).parent
    forbidden = ("import httpx", "import requests", "import socket", "urllib.request")
    for module in sorted(package_root.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{module.name} may reach the network via {needle!r}"

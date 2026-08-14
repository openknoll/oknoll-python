"""Locator test vectors: the fixture itself is a contract, so pin its shape.

The vectors in fixtures/locators/vectors.json are the normative companion to
spec/locator-grammar.md. The runtime locator parser must pass every case
verbatim; until it exists, this test keeps the fixture well-formed, unique,
and honest about the three outcome classes (valid / ambiguous / rejected).
"""

import json
import re
from pathlib import Path
from typing import Any

VECTORS = Path(__file__).resolve().parents[3] / "fixtures" / "locators" / "vectors.json"

KINDS = {"path", "local", "oci", "oknoll", "bare"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _cases() -> list[dict[str, Any]]:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert data["schema"] == "oknoll-locator-vectors-v1"
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def test_vectors_are_unique_and_classified() -> None:
    cases = _cases()
    inputs = [case["input"] for case in cases]
    assert len(inputs) == len(set(inputs)), "duplicate vector inputs"
    for case in cases:
        assert case["expect"] in {"valid", "ambiguous", "rejected"}, case["input"]


def test_all_three_outcome_classes_are_covered() -> None:
    outcomes = {case["expect"] for case in _cases()}
    assert outcomes == {"valid", "ambiguous", "rejected"}


def test_valid_and_ambiguous_cases_carry_wellformed_parses() -> None:
    for case in _cases():
        if case["expect"] == "rejected":
            assert case.get("reason"), f"rejected case without reason: {case['input']}"
            assert re.match(r"^[a-z][a-z0-9_]*$", case["reason"]), case["input"]
            assert "parsed" not in case, case["input"]
            continue
        parsed = case["parsed"]
        assert parsed["kind"] in KINDS, case["input"]
        if case["expect"] == "ambiguous":
            assert parsed["kind"] == "bare", case["input"]
        digest = parsed.get("digest")
        if digest is not None:
            assert DIGEST_RE.match(digest), case["input"]
        assert not (parsed.get("tag") and digest), f"tag+digest must be rejected: {case['input']}"


def test_no_bare_hash_or_revision_survives_as_valid() -> None:
    # The spec's rule 1: rev-… and bare hex are never a locator digest.
    for case in _cases():
        if case["expect"] == "valid":
            digest = case["parsed"].get("digest", "")
            assert "rev-" not in digest
            assert digest == "" or digest.startswith("sha256:")

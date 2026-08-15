"""The locator parser against the committed contract vectors — verbatim.

fixtures/locators/vectors.json is normative (spec/locator-grammar.md). Every
valid case must parse to exactly the recorded shape, every ambiguous case must
come back as a bare name that machine contexts reject, and every rejected case
must fail with exactly the recorded reason code.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from oknoll_runtime import LocatorError, parse_locator, parse_machine_locator

VECTORS = Path(__file__).resolve().parents[3] / "fixtures" / "locators" / "vectors.json"
CASES: list[dict[str, Any]] = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[repr(case["input"]) for case in CASES])
def test_vector(case: dict[str, Any]) -> None:
    if case["expect"] == "rejected":
        with pytest.raises(LocatorError) as excinfo:
            parse_locator(case["input"])
        assert excinfo.value.code == case["reason"], case["input"]
        return

    locator = parse_locator(case["input"])
    assert locator.to_dict() == case["parsed"], case["input"]

    if case["expect"] == "ambiguous":
        # Interactive contexts may resolve bare names; machine contexts must not.
        with pytest.raises(LocatorError) as excinfo:
            parse_machine_locator(case["input"])
        assert excinfo.value.code == "bare_name_not_allowed"
    else:
        assert parse_machine_locator(case["input"]) == locator


def test_hosts_normalize_to_lowercase() -> None:
    locator = parse_locator("oci://GHCR.io/acme/handbook:1.0")
    assert locator.host == "ghcr.io"
    assert locator.repository == "acme/handbook"

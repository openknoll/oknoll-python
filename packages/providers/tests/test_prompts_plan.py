"""The concept-plan template renders the outline the planner is allowed to see."""

from oknoll_providers.prompts import render


def test_concept_plan_renders_outline() -> None:
    system, user = render(
        "concept-plan",
        {
            "title": "Wire Protocol Spec",
            "sections": [
                {"index": 0, "heading": "Handshake", "snippet": "Clients open with HELLO."},
                {"index": 1, "heading": "Framing", "snippet": ""},
            ],
        },
    )
    assert '"concepts"' in system  # the JSON contract is spelled out
    assert "Document title: Wire Protocol Spec" in user
    assert "0. Handshake — Clients open with HELLO." in user
    assert "1. Framing" in user


def test_concept_plan_tolerates_malformed_payload() -> None:
    system, user = render("concept-plan", {"title": "", "sections": "nope"})
    assert "(untitled document)" in user
    assert "(no sections)" in user
    assert system

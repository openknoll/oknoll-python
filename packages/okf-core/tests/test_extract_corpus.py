"""Malicious-archive corpus — a release gate (`make security`).

Every archive in fixtures/security/archives/ encodes one ingestion attack;
expected.json pins the exact ExtractError code safe_extract_bundle must reject
it with. The corpus and the directory must stay in lockstep: an archive
without an expectation (or the reverse) fails the gate, so a new attack case
cannot land silently untested.

Resource-limit cases are sized against CORPUS_LIMITS so the fixtures stay
small; the enforcement code path is identical at the default limits.
"""

import json
from pathlib import Path

import pytest
from okf_core import ExtractError, ExtractLimits, safe_extract_bundle

ARCHIVES = Path(__file__).resolve().parents[3] / "fixtures" / "security" / "archives"
EXPECTED: dict[str, str] = json.loads((ARCHIVES / "expected.json").read_text(encoding="utf-8"))

# Tightened resource limits the sized fixtures (bomb, oversized member,
# member flood) are generated against — see scripts/gen_security_archives.py.
CORPUS_LIMITS = ExtractLimits(
    max_compressed_bytes=16 * 1024 * 1024,
    max_expanded_bytes=1024 * 1024,
    max_member_bytes=256 * 1024,
    max_file_count=64,
)


def test_corpus_and_expectations_are_in_lockstep() -> None:
    on_disk = {p.name for p in ARCHIVES.iterdir() if p.name != "expected.json"}
    assert on_disk == set(EXPECTED), "corpus archives and expected.json disagree"
    assert len(EXPECTED) >= 15  # the gate must not quietly shrink


@pytest.mark.parametrize("filename", sorted(EXPECTED))
def test_malicious_archive_is_rejected(filename: str, tmp_path: Path) -> None:
    dest = tmp_path / "bundle"
    with pytest.raises(ExtractError) as excinfo:
        safe_extract_bundle(ARCHIVES / filename, dest, CORPUS_LIMITS)
    assert excinfo.value.code == EXPECTED[filename], filename
    # Rejection must be total: no destination, no staging leftovers.
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []

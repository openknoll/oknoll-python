from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures" / "bundles"


@pytest.fixture()
def golden_dir() -> Path:
    return FIXTURES / "golden"


@pytest.fixture()
def malformed_dir() -> Path:
    return FIXTURES / "malformed"

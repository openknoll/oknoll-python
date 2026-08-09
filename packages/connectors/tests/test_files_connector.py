"""Files connector: shared contract suite + format-specific normalization."""

import shutil
from pathlib import Path

import pytest
from okf_core import CanonicalDoc, SourceRef
from oknoll_connectors import (
    ConnectorCheckpoint,
    ConnectorError,
    FetchPolicy,
    FilesConnector,
)
from oknoll_connectors.testing import ConnectorContractSuite, ConnectorFixture

REPO_ROOT = Path(__file__).resolve().parents[3]
HANDBOOK = REPO_ROOT / "fixtures" / "sources" / "handbook"
MALFORMED = REPO_ROOT / "fixtures" / "sources" / "malformed"

FIXED_CLOCK = "2026-08-03T00:00:00Z"


def make_connector(base_dir: Path, checkpoint: ConnectorCheckpoint | None = None) -> FilesConnector:
    return FilesConnector(base_dir=base_dir, clock=lambda: FIXED_CLOCK, checkpoint=checkpoint)


class TestFilesConnectorContract(ConnectorContractSuite):
    def make_fixture(self, tmp_path: Path) -> ConnectorFixture:
        shutil.copytree(HANDBOOK, tmp_path / "handbook")
        shutil.copytree(MALFORMED, tmp_path / "malformed")
        return ConnectorFixture(
            make_connector=lambda checkpoint: make_connector(tmp_path, checkpoint),
            source=SourceRef(connector="files", uri="handbook"),
            policy=FetchPolicy(),
            min_items=5,
            malformed_source=SourceRef(connector="files", uri="malformed"),
        )


@pytest.fixture()
def connector() -> FilesConnector:
    return make_connector(REPO_ROOT / "fixtures" / "sources")


def _docs_by_uri(connector: FilesConnector, uri: str) -> dict[str, CanonicalDoc]:
    items = list(connector.acquire(SourceRef(connector="files", uri=uri), FetchPolicy()))
    return {item.uri: next(iter(connector.normalize(item))) for item in items}


def test_markdown_normalization(connector: FilesConnector) -> None:
    docs = _docs_by_uri(connector, "handbook")
    doc = docs["handbook/welcome.md"]
    assert doc.title == "Team handbook"
    kinds = [b.kind for b in doc.blocks]
    assert kinds == [
        "heading",
        "paragraph",
        "paragraph",
        "heading",
        "paragraph",
        "code",
        "heading",
        "quote",
        "list",
    ]
    code = next(b for b in doc.blocks if b.kind == "code")
    assert code.language == "bash"
    assert code.text == "make test"
    anchor_ids = [a.id for a in doc.anchors]
    assert anchor_ids == ["team-handbook", "how-we-work", "escalation"]
    assert any(link.target == "security.md" for link in doc.links)


def test_text_normalization_line_anchors(connector: FilesConnector) -> None:
    docs = _docs_by_uri(connector, "handbook")
    doc = docs["handbook/meeting-notes.txt"]
    assert doc.title == "meeting notes"
    assert [a.kind for a in doc.anchors] == ["line", "line", "line"]
    assert [a.value for a in doc.anchors] == ["1", "3", "5"]


def test_pdf_normalization_page_anchors(connector: FilesConnector) -> None:
    docs = _docs_by_uri(connector, "handbook")
    doc = docs["handbook/release-process.pdf"]
    assert [a.kind for a in doc.anchors] == ["page", "page"]
    text = "\n".join(b.text for b in doc.blocks)
    assert "develop branch" in text
    assert "previous digest" in text


def test_docx_normalization_heading_levels(connector: FilesConnector) -> None:
    docs = _docs_by_uri(connector, "handbook")
    doc = docs["handbook/onboarding.docx"]
    assert doc.title == "Onboarding checklist"
    headings = [(b.level, b.text) for b in doc.blocks if b.kind == "heading"]
    assert headings == [
        (1, "Onboarding checklist"),
        (2, "Accounts"),
        (2, "Hardware"),
    ]


def test_probe_rejects_unsupported(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("a,b\n", encoding="utf-8")
    connector = make_connector(tmp_path)
    result = connector.probe(SourceRef(connector="files", uri="data.csv"))
    assert not result.supported
    missing = connector.probe(SourceRef(connector="files", uri="nope"))
    assert not missing.supported


def test_oversize_file_raises(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_text("x" * 100, encoding="utf-8")
    connector = make_connector(tmp_path)
    with pytest.raises(ConnectorError, match="max_item_bytes"):
        list(
            connector.acquire(
                SourceRef(connector="files", uri="big.md"), FetchPolicy(max_item_bytes=10)
            )
        )


def test_malformed_pdf_and_docx_raise(tmp_path: Path) -> None:
    shutil.copytree(MALFORMED, tmp_path / "malformed")
    connector = make_connector(tmp_path)
    for name in ("malformed/broken.pdf", "malformed/broken.docx"):
        item = next(iter(connector.acquire(SourceRef(connector="files", uri=name), FetchPolicy())))
        with pytest.raises(ConnectorError):
            list(connector.normalize(item))


def test_ignore_patterns(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text("# Keep\n", encoding="utf-8")
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "skip.md").write_text("# Skip\n", encoding="utf-8")
    connector = FilesConnector(
        base_dir=tmp_path, clock=lambda: FIXED_CLOCK, ignore_patterns=("drafts/",)
    )
    items = list(connector.acquire(SourceRef(connector="files", uri="."), FetchPolicy()))
    assert [item.uri for item in items] == ["keep.md"]


def test_checkpoint_from_other_connector_rejected() -> None:
    foreign = ConnectorCheckpoint(connector="web", version="0.1.0", cursor={"last_uri": "x"})
    with pytest.raises(ConnectorError, match="belongs to connector"):
        FilesConnector(checkpoint=foreign)

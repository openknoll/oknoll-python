"""Build progress reporting: `build_revision(on_progress=…)`.

The callback is presentation only — slow steps (acquisition, model calls)
announce themselves *before* they start so a stalled build names its current
work item, and passing a callback never changes the built bytes.
"""

from pathlib import Path

from okf_core import (
    BuildOutcome,
    PipelineSource,
    SourceRef,
    StubModelProvider,
    build_revision,
)
from oknoll_connectors import FetchPolicy, FilesConnector

FIXED_CLOCK = "2026-08-03T00:00:00Z"

DOC_MD = """\
# Payments

Refunds are processed within 5 business days.
"""


def _build(root: Path, on_progress: object = None) -> BuildOutcome:
    return build_revision(
        bundle_dir=root / "bundle",
        project_name="progress",
        sources=[
            PipelineSource(
                source=SourceRef(connector="files", uri="sources"),
                connector=FilesConnector(base_dir=root, clock=lambda: FIXED_CLOCK),
                policy=FetchPolicy(),
            )
        ],
        provider=StubModelProvider(),
        clock=lambda: FIXED_CLOCK,
        on_progress=on_progress,  # type: ignore[arg-type]
    )


def _write_sources(root: Path) -> None:
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "payments.md").write_text(DOC_MD, encoding="utf-8")


def test_progress_reports_every_stage_in_order(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    lines: list[str] = []
    outcome = _build(tmp_path, on_progress=lines.append)
    assert outcome.published

    stages = [line.split(":", 1)[0] for line in lines]
    # Acquisition announces before and after; the model stages announce each
    # work item; finalize closes the run.
    assert stages[0] == "acquire"
    for expected in ("acquire", "normalize", "plan", "generate", "finalize"):
        assert expected in stages, lines
    assert any(line.startswith("acquire: files sources → 1 file(s)") for line in lines)
    assert any(line.startswith("plan: [1/1]") for line in lines)
    assert any("reference [1/1]" in line for line in lines)
    assert any("concept [1/1]" in line for line in lines)
    assert any(line.startswith("generate: done — cache") for line in lines)
    # Stage order is the pipeline order.
    assert stages.index("plan") > stages.index("normalize")
    assert stages.index("finalize") > stages.index("generate")


def test_progress_callback_never_changes_the_built_bytes(tmp_path: Path) -> None:
    silent_root = tmp_path / "silent"
    chatty_root = tmp_path / "chatty"
    for root in (silent_root, chatty_root):
        root.mkdir()
        _write_sources(root)

    silent = _build(silent_root)
    chatty = _build(chatty_root, on_progress=lambda _line: None)
    assert silent.revision_id == chatty.revision_id

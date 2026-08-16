"""Adopt an existing OKF bundle tree as a published revision.

Import is publish machinery only: content files are copied byte-verbatim
(exactly the set the revision id and manifest hash, so "verbatim" and
"deterministic" coincide), a foreign ``log.md`` is preserved as the parent
log, the manifest is recomputed, and the same lint gate build uses decides
whether anything touches the target bundle directory. Staging happens in a
temporary directory — the target is only written after lint passes.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from okf_core import bundle as bundle_mod
from okf_core import indexing
from okf_core.findings import LintReport
from okf_core.lint import lint_bundle
from okf_core.revision import (
    LOG_HEADER,
    compute_revision_id,
    log_with_entry,
    publish_revision,
    write_manifest,
)


class BundleImportError(RuntimeError):
    """The source tree cannot be imported (missing, empty, or unreadable)."""


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    revision_id: str
    file_count: int
    lint_report: LintReport
    published: bool
    staged_dir: Path | None  # retained for inspection when lint blocked publish


def import_bundle(
    source_dir: Path,
    bundle_dir: Path,
    *,
    summary: str,
    stage_root: Path | None = None,
) -> ImportOutcome:
    """Stage, lint, and publish ``source_dir`` as a revision of ``bundle_dir``.

    Idempotent by construction: the same source tree yields the same revision
    id, and published revisions are never rewritten. When lint reports errors
    the outcome carries the retained stage for inspection and ``bundle_dir``
    is left untouched.
    """
    if not source_dir.is_dir():
        raise BundleImportError(f"{source_dir} is not a directory")
    files = bundle_mod.iter_files(source_dir)
    if not files:
        raise BundleImportError(f"no bundle content files under {source_dir}")

    stage = Path(tempfile.mkdtemp(prefix="oknoll-import-", dir=stage_root))
    for file in files:
        target = stage / file.rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file.abs_path.read_bytes())

    revision_id = compute_revision_id(stage)

    log_path = stage / bundle_mod.LOG_NAME
    parent_log = log_path.read_text(encoding="utf-8") if log_path.is_file() else LOG_HEADER
    log_path.write_text(log_with_entry(parent_log, revision_id, summary), encoding="utf-8")
    write_manifest(stage)

    lint_report = lint_bundle(stage)
    if not lint_report.passed():
        return ImportOutcome(
            revision_id=revision_id,
            file_count=len(files),
            lint_report=lint_report,
            published=False,
            staged_dir=stage,
        )

    bundle_dir.mkdir(parents=True, exist_ok=True)
    indexing.write_index(stage, indexing.index_dir_for(bundle_dir, revision_id))
    publish_revision(bundle_dir, stage, revision_id)
    shutil.rmtree(stage, ignore_errors=True)
    return ImportOutcome(
        revision_id=revision_id,
        file_count=len(files),
        lint_report=lint_report,
        published=True,
        staged_dir=None,
    )

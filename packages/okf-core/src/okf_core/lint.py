"""Five-level bundle lint engine.

Levels: conformance and integrity fail builds (errors); provenance/trust
defaults to warnings with per-finding severity; hygiene warns (fails under
strict); quality only reports. Works on any bundle directory, including bundles
produced by others — unknown types and unknown frontmatter keys are tolerated.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from okf_core import bundle as bundle_mod
from okf_core import links as links_mod
from okf_core.findings import Finding, Level, LintReport, Severity
from okf_core.frontmatter import (
    STATUS_VALUES,
    Frontmatter,
    FrontmatterShapeError,
    FrontmatterYamlError,
    ParsedDocument,
    parse_document,
)


@dataclass(frozen=True, slots=True)
class LintConfig:
    max_concept_chars: int = 50_000


@dataclass(frozen=True, slots=True)
class _ParsedFile:
    rel_path: str
    doc: ParsedDocument
    internal_targets: tuple[str, ...]  # resolved bundle-root-relative link targets


def lint_bundle(root: Path, config: LintConfig | None = None) -> LintReport:
    config = config or LintConfig()
    report = LintReport(bundle_path=str(root))

    if not root.is_dir():
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/not-a-bundle",
                "",
                f"{root} is not a directory",
            )
        )
        return report

    files = bundle_mod.iter_files(root)
    rel_paths = {f.rel_path for f in files}

    if bundle_mod.INDEX_NAME not in rel_paths:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/missing-index",
                "",
                "bundle has no root index.md",
            )
        )
    if bundle_mod.LOG_NAME not in rel_paths:
        report.add(
            Finding(
                Level.HYGIENE,
                Severity.WARNING,
                "hygiene/missing-log",
                "",
                "bundle has no revision-level log.md",
            )
        )

    _check_manifest(root, rel_paths, report)

    parsed: list[_ParsedFile] = []
    for file in bundle_mod.markdown_files(files):
        if bundle_mod.is_reference(file.rel_path):
            continue  # acquired source material, not subject to concept lint
        parsed_file = _lint_markdown_file(file, report, config)
        if parsed_file is not None:
            parsed.append(parsed_file)

    _check_link_graph(parsed, rel_paths, root, report)
    return report


def _check_manifest(root: Path, rel_paths: set[str], report: LintReport) -> None:
    manifest_path = root / bundle_mod.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/invalid-manifest",
                bundle_mod.MANIFEST_NAME,
                f"manifest.json is not valid JSON: {exc}",
            )
        )
        return

    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, dict):
        return  # permissive: foreign manifest shapes are tolerated

    for entry_path in sorted(entries):
        spec = entries[entry_path]
        normalized = posixpath.normpath(entry_path.lstrip("/"))
        if entry_path.startswith("/") or normalized.startswith(".."):
            report.add(
                Finding(
                    Level.INTEGRITY,
                    Severity.ERROR,
                    "integrity/path-traversal",
                    bundle_mod.MANIFEST_NAME,
                    f"manifest entry escapes the bundle root: {entry_path}",
                )
            )
            continue
        if normalized not in rel_paths:
            report.add(
                Finding(
                    Level.INTEGRITY,
                    Severity.ERROR,
                    "integrity/manifest-missing-file",
                    bundle_mod.MANIFEST_NAME,
                    f"manifest lists a file that does not exist: {entry_path}",
                )
            )
            continue
        expected = spec.get("sha256") if isinstance(spec, dict) else None
        if isinstance(expected, str):
            actual = hashlib.sha256((root / normalized).read_bytes()).hexdigest()
            if actual != expected:
                report.add(
                    Finding(
                        Level.INTEGRITY,
                        Severity.ERROR,
                        "integrity/checksum-mismatch",
                        normalized,
                        f"sha256 mismatch: manifest says {expected}, file is {actual}",
                    )
                )


def _lint_markdown_file(
    file: bundle_mod.BundleFile, report: LintReport, config: LintConfig
) -> _ParsedFile | None:
    rel = file.rel_path
    try:
        text = file.abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/invalid-encoding",
                rel,
                "file is not valid UTF-8",
            )
        )
        return None

    try:
        doc = parse_document(text)
    except FrontmatterYamlError as exc:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/unparseable-frontmatter",
                rel,
                str(exc),
            )
        )
        return None
    except FrontmatterShapeError as exc:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/invalid-frontmatter-shape",
                rel,
                str(exc),
            )
        )
        return None

    if bundle_mod.is_concept(rel):
        _lint_concept(rel, doc, report, config)

    targets = _resolve_links(rel, doc.body, report)
    return _ParsedFile(rel_path=rel, doc=doc, internal_targets=tuple(targets))


def _lint_concept(rel: str, doc: ParsedDocument, report: LintReport, config: LintConfig) -> None:
    fm = doc.frontmatter
    body = doc.body

    if fm is None:
        report.add(
            Finding(
                Level.CONFORMANCE,
                Severity.ERROR,
                "conformance/missing-frontmatter",
                rel,
                "concept file has no frontmatter block",
            )
        )
    else:
        if not (isinstance(fm.data.get("type"), str) and fm.data["type"].strip()):
            report.add(
                Finding(
                    Level.CONFORMANCE,
                    Severity.ERROR,
                    "conformance/missing-type",
                    rel,
                    "concept frontmatter has no non-empty `type`",
                )
            )
        _lint_provenance(rel, fm, report)
        if fm.title is None or not fm.title.strip():
            report.add(
                Finding(
                    Level.HYGIENE,
                    Severity.WARNING,
                    "hygiene/missing-title",
                    rel,
                    "concept frontmatter has no title",
                )
            )
        if not fm.sources:
            report.add(
                Finding(
                    Level.QUALITY,
                    Severity.INFO,
                    "quality/no-sources",
                    rel,
                    "concept declares no sources (source coverage)",
                )
            )

    if not body.strip():
        report.add(
            Finding(
                Level.HYGIENE, Severity.WARNING, "hygiene/empty-body", rel, "concept body is empty"
            )
        )
    if len(body) > config.max_concept_chars:
        report.add(
            Finding(
                Level.QUALITY,
                Severity.INFO,
                "quality/large-concept",
                rel,
                f"concept body is {len(body)} chars (threshold {config.max_concept_chars})",
            )
        )

    _lint_footnotes(rel, fm, body, report)


def _lint_provenance(rel: str, fm: Frontmatter, report: LintReport) -> None:
    status = fm.data.get("status")
    if status is not None and (not isinstance(status, str) or status not in STATUS_VALUES):
        report.add(
            Finding(
                Level.PROVENANCE,
                Severity.WARNING,
                "provenance/invalid-status",
                rel,
                f"status {status!r} is not one of {', '.join(STATUS_VALUES)}",
            )
        )

    generated = fm.data.get("generated")
    if generated is not None and not _valid_actor_event(generated):
        report.add(
            Finding(
                Level.PROVENANCE,
                Severity.WARNING,
                "provenance/invalid-generated",
                rel,
                "`generated` must be a mapping with string `by` and ISO-8601 `at`",
            )
        )

    verified = fm.data.get("verified")
    if verified is not None:
        events = verified if isinstance(verified, list) else [verified]
        for event in events:
            if not _valid_actor_event(event):
                report.add(
                    Finding(
                        Level.PROVENANCE,
                        Severity.WARNING,
                        "provenance/invalid-verified",
                        rel,
                        "`verified` events need string `by` and ISO-8601 `at`",
                    )
                )
                break

    raw_sources = fm.data.get("sources")
    if raw_sources is not None:
        if not isinstance(raw_sources, list):
            report.add(
                Finding(
                    Level.PROVENANCE,
                    Severity.WARNING,
                    "provenance/malformed-source",
                    rel,
                    "`sources` must be a list of {id, resource, title} entries",
                )
            )
        else:
            seen_ids: set[str] = set()
            for entry in raw_sources:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                    report.add(
                        Finding(
                            Level.PROVENANCE,
                            Severity.WARNING,
                            "provenance/malformed-source",
                            rel,
                            f"source entry lacks a string `id`: {entry!r}",
                        )
                    )
                    continue
                source_id = entry["id"]
                if source_id in seen_ids:
                    report.add(
                        Finding(
                            Level.INTEGRITY,
                            Severity.ERROR,
                            "integrity/duplicate-id",
                            rel,
                            f"duplicate source id {source_id!r}",
                        )
                    )
                seen_ids.add(source_id)
                resource = entry.get("resource")
                if resource is not None and isinstance(resource, str):
                    normalized = posixpath.normpath(resource.lstrip("/"))
                    if resource.startswith(("/", "\\")) or normalized.startswith(".."):
                        report.add(
                            Finding(
                                Level.INTEGRITY,
                                Severity.ERROR,
                                "integrity/path-traversal",
                                rel,
                                f"source {source_id!r} resource escapes the bundle root: "
                                f"{resource}",
                            )
                        )

    stale_after = fm.data.get("stale_after")
    if stale_after is not None and not _valid_iso_date(stale_after):
        report.add(
            Finding(
                Level.PROVENANCE,
                Severity.WARNING,
                "provenance/invalid-stale-after",
                rel,
                f"stale_after {stale_after!r} is not an ISO-8601 date",
            )
        )


def _lint_footnotes(rel: str, fm: Frontmatter | None, body: str, report: LintReport) -> None:
    refs = links_mod.footnote_references(body)
    defs = links_mod.footnote_definitions(body)
    source_ids = set(fm.source_ids()) if fm is not None else set()

    for ref in sorted(refs):
        if ref not in defs:
            report.add(
                Finding(
                    Level.HYGIENE,
                    Severity.WARNING,
                    "hygiene/missing-footnote",
                    rel,
                    f"footnote [^{ref}] is referenced but never defined",
                )
            )
        if ref.startswith("source-") and ref not in source_ids:
            report.add(
                Finding(
                    Level.PROVENANCE,
                    Severity.WARNING,
                    "provenance/unsupported-claim",
                    rel,
                    f"claim cites [^{ref}] but frontmatter declares no source {ref!r}",
                )
            )


def _resolve_links(rel: str, body: str, report: LintReport) -> list[str]:
    targets: list[str] = []
    for link in links_mod.extract_links(body):
        if links_mod.is_external(link.target):
            continue
        resolved = links_mod.resolve_target(link.target, rel)
        if resolved is None:
            report.add(
                Finding(
                    Level.INTEGRITY,
                    Severity.ERROR,
                    "integrity/path-traversal",
                    rel,
                    f"link escapes the bundle root: {link.target}",
                )
            )
            continue
        targets.append(resolved)
    return targets


def _check_link_graph(
    parsed: list[_ParsedFile], rel_paths: set[str], root: Path, report: LintReport
) -> None:
    inbound: set[str] = set()
    index_targets: set[str] = set()

    for file in parsed:
        for target in file.internal_targets:
            inbound.add(target)
            if bundle_mod.is_index(file.rel_path):
                index_targets.add(target)
            if target not in rel_paths and not (root / target).is_dir():
                report.add(
                    Finding(
                        Level.HYGIENE,
                        Severity.WARNING,
                        "hygiene/broken-link",
                        file.rel_path,
                        f"link target does not exist: {target}",
                    )
                )

    for file in parsed:
        if not bundle_mod.is_concept(file.rel_path):
            continue
        if file.rel_path not in inbound:
            report.add(
                Finding(
                    Level.HYGIENE,
                    Severity.WARNING,
                    "hygiene/orphan-concept",
                    file.rel_path,
                    "concept has no inbound links",
                )
            )
        elif file.rel_path not in index_targets:
            report.add(
                Finding(
                    Level.QUALITY,
                    Severity.INFO,
                    "quality/unindexed-concept",
                    file.rel_path,
                    "concept is not linked from any index",
                )
            )
        if not file.internal_targets:
            report.add(
                Finding(
                    Level.QUALITY,
                    Severity.INFO,
                    "quality/no-outbound-links",
                    file.rel_path,
                    "concept links to nothing else in the bundle",
                )
            )

    for file in parsed:
        fm = file.doc.frontmatter
        if fm is None or not bundle_mod.is_concept(file.rel_path):
            continue
        for entry in fm.sources:
            resource = entry.get("resource")
            if isinstance(resource, str):
                normalized = posixpath.normpath(resource.lstrip("/"))
                if not normalized.startswith("..") and normalized not in rel_paths:
                    report.add(
                        Finding(
                            Level.PROVENANCE,
                            Severity.WARNING,
                            "provenance/missing-source-resource",
                            file.rel_path,
                            f"source resource does not exist: {resource}",
                        )
                    )


def _valid_actor_event(event: Any) -> bool:
    return (
        isinstance(event, dict)
        and isinstance(event.get("by"), str)
        and bool(event["by"].strip())
        and isinstance(event.get("at"), str)
        and _valid_iso_datetime(event["at"])
    )


def _valid_iso_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _valid_iso_date(value: Any) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return _valid_iso_datetime(value)
    return True

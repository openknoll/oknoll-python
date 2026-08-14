"""Lint findings and reports.

Severity contract: conformance and integrity findings are errors and fail a
build; provenance/trust findings default to warnings (severity per finding);
hygiene warns and fails only under --strict; quality is informational.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Level(StrEnum):
    CONFORMANCE = "conformance"
    INTEGRITY = "integrity"
    PROVENANCE = "provenance"
    HYGIENE = "hygiene"
    QUALITY = "quality"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    severity: Severity
    code: str  # stable machine code, e.g. "integrity/path-traversal"
    path: str  # bundle-root-relative posix path; "" for bundle-level findings
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level.value,
            "severity": self.severity.value,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass
class LintReport:
    bundle_path: str
    findings: list[Finding] = field(default_factory=list)
    # Deterministic bundle-health aggregates (counts and ratios computed from
    # the same pass that produced the findings) — reported, never gating.
    metrics: dict[str, Any] = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (f.path, f.code, f.message))

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity is severity)

    def codes(self) -> set[str]:
        return {f.code for f in self.findings}

    def passed(self, *, strict: bool = False) -> bool:
        if self.count(Severity.ERROR):
            return False
        return not (strict and self.count(Severity.WARNING))

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle_path,
            "summary": {
                "errors": self.count(Severity.ERROR),
                "warnings": self.count(Severity.WARNING),
                "info": self.count(Severity.INFO),
            },
            "findings": [f.to_dict() for f in self.sorted_findings()],
            "metrics": self.metrics,
        }

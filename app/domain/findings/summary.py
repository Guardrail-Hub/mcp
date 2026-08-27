"""FindingSummary and OverallRisk (Decision 0008 sections 4.2, 4.3).

The count of findings by severity, and the one-line posture answer derived
from it.

**This module settles a live production conflict.** Two different meanings of
"total" existed simultaneously: ``ZapScanSummary.total`` counted every alert
(informational included) while ``ScanFindings.total`` counted vulnerabilities
only (informational excluded) — the same scan reported different numbers on
different surfaces. The defect was two concepts sharing one name, so the
language gives them distinct names and retires the ambiguous word:

    finding_count       — every finding, informational included.
    vulnerability_count — findings whose severity is a vulnerability (I13).

No domain term named ``total`` exists, by decision.

The breakdown is a **mapping keyed by Severity**, never fixed integer fields,
so extending the severity set stays additive (invariant I12).

Invariants:
    I16 — finding_count >= vulnerability_count, always.
    I17 — overall_risk is the most severe vulnerability present, or ``None``
          when vulnerability_count is 0.
"""

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from app.domain.findings.finding import Finding
from app.domain.findings.severity import SEVERITY_ORDER, Severity


@dataclass(frozen=True, slots=True)
class FindingSummary:
    """How many findings there are, by canonical severity."""

    counts: Mapping[Severity, int]

    @classmethod
    def from_counts(cls, counts: Mapping[Severity, int]) -> "FindingSummary":
        """Build from a severity->count mapping, defaulting absent severities to 0."""
        return cls(counts={s: int(counts.get(s, 0) or 0) for s in SEVERITY_ORDER})

    @classmethod
    def empty(cls) -> "FindingSummary":
        """A summary with every severity at zero."""
        return cls(counts={s: 0 for s in SEVERITY_ORDER})

    @classmethod
    def from_findings(cls, findings: Iterable[Finding]) -> "FindingSummary":
        """Count *findings* by severity."""
        counts = {s: 0 for s in SEVERITY_ORDER}
        for finding in findings:
            counts[finding.severity] += 1
        return cls(counts=counts)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "FindingSummary":
        """Build from a loose string-keyed mapping (e.g. an event payload).

        Unrecognised keys are ignored and unparseable values count as 0, so a
        malformed payload degrades to an accurate-as-possible summary rather
        than raising inside a notification path.
        """
        counts = {s: 0 for s in SEVERITY_ORDER}
        for severity in SEVERITY_ORDER:
            raw = data.get(severity.value)
            try:
                counts[severity] = int(raw) if raw is not None else 0
            except (TypeError, ValueError):
                counts[severity] = 0
        return cls(counts=counts)

    def count_of(self, severity: Severity) -> int:
        """Number of findings at *severity*."""
        return self.counts.get(severity, 0)

    @property
    def finding_count(self) -> int:
        """Every finding, informational included."""
        return sum(self.counts.values())

    @property
    def vulnerability_count(self) -> int:
        """Findings whose severity is a vulnerability — excludes INFORMATIONAL."""
        return sum(
            count
            for severity, count in self.counts.items()
            if severity.is_vulnerability
        )

    @property
    def has_vulnerabilities(self) -> bool:
        """Whether any non-informational finding is present."""
        return self.vulnerability_count > 0

    @property
    def overall_risk(self) -> Optional[Severity]:
        """The most severe vulnerability present, or ``None`` (invariant I17).

        Informational findings never constitute risk, so a scan with only
        informational results reports no overall risk.
        """
        for severity in SEVERITY_ORDER:
            if severity.is_vulnerability and self.counts.get(severity, 0) > 0:
                return severity
        return None

    def as_mapping(self) -> dict:
        """Plain ``{severity_value: count}`` for transport/persistence payloads."""
        return {severity.value: count for severity, count in self.counts.items()}

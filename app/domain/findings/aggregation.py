"""Aggregation rules over findings (Decision 0008 sections 4.1, 4.3).

The business rules for collapsing many raw findings into a ranked, deduplicated
view: findings sharing an identity are the same issue and are counted, not
repeated (invariant I15).

Contains rules only — no rendering, no persistence, no tool vocabulary. Display
labels, Markdown/SARIF formatting, and remediation prose belong to the layers
above this one.
"""

from dataclasses import dataclass
from typing import Iterable, List, Optional

from app.domain.findings.finding import Finding, FindingIdentity
from app.domain.findings.severity import Severity


@dataclass(frozen=True, slots=True)
class AggregatedFinding:
    """One distinct issue, with how many times it was observed.

    Attributes:
        identity: The grouping key these occurrences shared.
        count: How many raw findings collapsed into this one.
        example_location: A representative location, for reporting.
        remediation: Fix guidance carried from the first occurrence that had it.
    """

    identity: FindingIdentity
    count: int
    example_location: Optional[str] = None
    remediation: Optional[str] = None

    @property
    def rule_id(self) -> str:
        return self.identity.rule_id

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def severity(self) -> Severity:
        return self.identity.severity


def deduplicate(findings: Iterable[Finding]) -> List[AggregatedFinding]:
    """Collapse *findings* by identity, ranked most severe and most frequent first.

    Ordering is severity rank first, then descending occurrence count — the
    prioritisation a reader needs: the worst issues first, and among equals the
    most widespread first.
    """
    grouped: dict[FindingIdentity, dict] = {}
    for finding in findings:
        identity = finding.identity
        entry = grouped.get(identity)
        if entry is None:
            grouped[identity] = {
                "count": 1,
                "example_location": finding.location,
                "remediation": finding.remediation or None,
            }
            continue
        entry["count"] += 1
        if entry["example_location"] is None:
            entry["example_location"] = finding.location
        if not entry["remediation"] and finding.remediation:
            entry["remediation"] = finding.remediation

    aggregated = [
        AggregatedFinding(
            identity=identity,
            count=entry["count"],
            example_location=entry["example_location"],
            remediation=entry["remediation"],
        )
        for identity, entry in grouped.items()
    ]
    aggregated.sort(key=lambda item: (item.severity.rank, -item.count))
    return aggregated

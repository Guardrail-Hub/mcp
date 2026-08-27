"""Finding and its identity (Decision 0008 sections 3.3, 4.1, 4.4, 4.5).

A Finding is one issue a tool detected, in one place, attributable to one rule.
Tool-neutral: a scanner's native alert shape (e.g. ``ZapAlert``) is a wire
format that maps *into* this concept at the adapter boundary.

Invariants (Decision 0008):
    I8  — exactly one Severity.
    I9  — has an identity stable across repeated runs of the same tool against
          the same target; this is what makes deduplication meaningful.
    I10 — immutable once produced. Re-scanning creates new findings; it never
          mutates old ones. (Suppression/acceptance is a future concept and is
          deliberately not an attribute here, so immutability holds.)
    I18 — confidence never modifies severity; they are independent axes.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.domain.findings.severity import Severity


class Confidence(str, Enum):
    """How certain the tool is that a finding is real (Decision 0008 4.4).

    Orthogonal to :class:`Severity` (invariant I18): a *tentative* critical is
    still critical. Used for triage filtering, never to downgrade severity.
    """

    CONFIRMED = "confirmed"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def parse(cls, value: object) -> Optional["Confidence"]:
        """Coerce a loose value into a Confidence, or ``None`` if unrecognised."""
        if isinstance(value, cls):
            return value
        if value is None:
            return None
        text = str(value).strip().lower()
        for member in cls:
            if member.value == text:
                return member
        return None


@dataclass(frozen=True, slots=True)
class FindingIdentity:
    """Answers "are these two findings the same issue?" (Decision 0008 4.1).

    Derived from the rule that produced the finding plus where it occurred.
    Two findings with equal identity are the same issue and may be collapsed
    into one with an instance count (invariant I15).
    """

    rule_id: str
    name: str
    severity: Severity


@dataclass(frozen=True, slots=True)
class Finding:
    """One issue detected by one tool, in one place, from one rule.

    Attributes:
        rule_id: Stable identifier of the rule that produced this finding.
        name: Human-readable name of the issue.
        severity: Canonical severity (invariant I8).
        location: Where the finding is. Today always a URL — the only tool is
            dynamic (Decision 0008 section 4.5 names the concept so ``url`` does
            not become domain vocabulary; it stays a single opaque value until a
            second location shape actually exists).
        confidence: Optional certainty from the tool.
        description: Optional detail.
        remediation: Optional fix guidance supplied by the tool as *data*.
            Generic severity prose is presentation copy and does not live here.
    """

    rule_id: str
    name: str
    severity: Severity
    location: Optional[str] = None
    confidence: Optional[Confidence] = None
    description: Optional[str] = None
    remediation: Optional[str] = None

    @property
    def identity(self) -> FindingIdentity:
        """The grouping key for deduplication (invariant I9)."""
        return FindingIdentity(
            rule_id=self.rule_id, name=self.name, severity=self.severity
        )

"""Severity — how serious a finding is (Decision 0008 section 3.4).

The canonical, tool-independent seriousness of a Finding. Canonical *by
definition*: a tool's native scale (ZAP's ``High``, Semgrep's ``ERROR``,
Trivy's ``UNKNOWN``) is a wire format belonging to that tool's adapter, not a
domain concept. Translating a native scale into this one is a boundary
responsibility of the adapter — there is no second "normalized" severity type.

Invariants (Decision 0008):
    I11 — totally ordered: any two severities compare.
    I12 — the member set is closed and extended only additively.
    I13 — INFORMATIONAL is not a vulnerability.
    I14 — every Finding maps to exactly one member.
"""

from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """Canonical severity. Ordered most severe first (invariant I11)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"

    @property
    def rank(self) -> int:
        """Sort rank, ``0`` = most severe. The basis of the total ordering."""
        return _RANKS[self]

    @property
    def is_vulnerability(self) -> bool:
        """Whether this severity counts as a vulnerability (invariant I13).

        INFORMATIONAL findings are hardening opportunities, not vulnerabilities;
        this rule is what separates ``vulnerability_count`` from
        ``finding_count`` in :class:`~app.domain.findings.summary.FindingSummary`.
        """
        return self is not Severity.INFORMATIONAL

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        # Higher severity sorts first, so a lower rank is "less than".
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank >= other.rank

    @classmethod
    def parse(cls, value: object, default: Optional["Severity"] = None) -> "Severity":
        """Coerce a loose value (case-insensitive name) into a Severity.

        Used by tool adapters translating a native scale. Unrecognised values
        fall back to *default* (``INFORMATIONAL`` when unspecified) rather than
        raising, so one malformed record from a scanner cannot fail a whole
        report — the conservative choice for a security tool is to record the
        finding at the lowest severity, never to drop it.
        """
        if isinstance(value, cls):
            return value
        if value is not None:
            text = str(value).strip().lower()
            for member in cls:
                if member.value == text:
                    return member
        return default if default is not None else cls.INFORMATIONAL


# Rank map built once from declaration order (most severe first).
_RANKS = {member: index for index, member in enumerate(Severity)}

# Declaration order, most severe first. The canonical ordering for any display
# or iteration that needs every severity.
SEVERITY_ORDER = tuple(Severity)

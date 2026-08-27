"""Findings capability — what a security finding is, and how findings aggregate.

Owner: Findings capability (Decision 0008 section 7). This is the canonical and
only definition of severity in Guardrail Hub.

Allowed here: :class:`Severity` and its ordering, :class:`Finding`,
:class:`Confidence`, :class:`FindingIdentity`, :class:`FindingSummary` (with
``finding_count`` / ``vulnerability_count``), overall risk, and deduplication
rules.

Explicitly NOT allowed: Slack or Block Kit formatting; emoji; Markdown, SARIF
or JSON rendering; Pydantic request/response models; DAO or persistence models;
FastAPI types; tool-specific alert shapes (a scanner's native alert maps *into*
:class:`Finding` at its adapter); file paths, URLs as vocabulary, settings, or
logging.

This capability does **not** depend on :mod:`app.domain.lifecycle`, and must not
— a finding's severity and identity are true regardless of an operation's phase
(Decision 0008 section 8).
"""

from app.domain.findings.aggregation import AggregatedFinding, deduplicate
from app.domain.findings.finding import Confidence, Finding, FindingIdentity
from app.domain.findings.severity import SEVERITY_ORDER, Severity
from app.domain.findings.summary import FindingSummary

__all__ = [
    "SEVERITY_ORDER",
    "AggregatedFinding",
    "Confidence",
    "Finding",
    "FindingIdentity",
    "FindingSummary",
    "Severity",
    "deduplicate",
]

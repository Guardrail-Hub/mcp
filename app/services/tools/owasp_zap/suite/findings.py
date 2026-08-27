"""Suite report shaping — the executive view over aggregated child findings.

Severity, ordering, and deduplication are **owned by the domain layer**
(``app.domain.findings``, Decision 0008). This module is the suite's adapter
onto that domain: it maps ZAP alert payloads into canonical
:class:`~app.domain.findings.Finding` values, applies the domain rules, and
shapes the result back into the report's wire format.

Two things deliberately stay here rather than moving to the domain:

* **Display labels** (``"High"``) — the persisted report format capitalises
  severity; the domain value is lowercase. Label mapping is presentation.
* **Severity advice prose** — generic AppSec guidance is presentation copy, not
  domain knowledge (a package scanner's real advice is "upgrade X to Y", which
  tools supply as data). See the growth stress-test, amendment 4.
"""

from typing import Any, Optional

from app.domain.findings import (
    SEVERITY_ORDER,
    Finding,
    FindingSummary,
    Severity,
    deduplicate,
)

# Canonical severity -> the label used in the persisted report format.
_SEVERITY_LABEL = {
    Severity.CRITICAL: "Critical",
    Severity.HIGH: "High",
    Severity.MEDIUM: "Medium",
    Severity.LOW: "Low",
    Severity.INFORMATIONAL: "Informational",
}

# Display order for the report's risk vocabulary (most severe first).
RISK_ORDER = [_SEVERITY_LABEL[s] for s in SEVERITY_ORDER if s is not Severity.CRITICAL]

# Generic, human-readable guidance per severity present in the results.
# Presentation copy, intentionally not domain knowledge.
_SEVERITY_ADVICE = {
    "high": (
        "Address High-risk findings before release — these are exploitable "
        "vulnerabilities (e.g. injection, broken auth)."
    ),
    "medium": (
        "Plan remediation for Medium-risk findings; they often become exploitable "
        "when combined with other weaknesses."
    ),
    "low": "Review Low-risk findings and fix where inexpensive; track the rest.",
    "informational": (
        "Informational items are hardening opportunities (headers, disclosure); "
        "adopt them as time allows."
    ),
}


def severity_label(severity: Severity) -> str:
    """The report-format label for a canonical severity."""
    return _SEVERITY_LABEL[severity]


def risk_rank(risk: str) -> int:
    """Return a sort rank for a risk label (0 = most severe).

    Delegates ordering to the domain; retained as the suite's label-facing entry
    point.
    """
    return Severity.parse(risk).rank


def overall_risk(severity_summary: dict) -> str:
    """Return the highest severity present, or ``"None"`` when there are none.

    Informational findings never constitute risk (domain invariant I13), so a
    result containing only informational items reports ``"None"``.
    """
    risk = FindingSummary.from_mapping(severity_summary).overall_risk
    return _SEVERITY_LABEL[risk] if risk is not None else "None"


def _to_finding(alert: dict, fallback_location: Optional[str] = None) -> Finding:
    """Map one persisted ZAP alert payload onto the canonical Finding.

    This is the tool boundary: ZAP's native risk scale is translated into the
    canonical severity here, never inside the domain.
    """
    rule_id = str(alert.get("alert_ref") or alert.get("name") or "zap")
    return Finding(
        rule_id=rule_id,
        name=alert.get("name", rule_id),
        severity=Severity.parse(alert.get("risk")),
        location=alert.get("url") or fallback_location,
        remediation=alert.get("solution") or "",
    )


def collect_findings(outcomes: list) -> list[dict]:
    """Deduplicate child alerts into ranked detailed findings.

    Findings sharing an identity are collapsed and counted by the domain; the
    affected suite categories are re-attached here because "category" is a suite
    concept, not a domain one.
    """
    findings: list[Finding] = []
    categories_by_identity: dict[Any, set] = {}

    for outcome in outcomes:
        for alert in outcome.alerts:
            finding = _to_finding(alert, fallback_location=outcome.url)
            findings.append(finding)
            categories_by_identity.setdefault(finding.identity, set()).add(
                outcome.category
            )

    return [
        {
            "rule_id": aggregated.rule_id,
            "name": aggregated.name,
            "risk": _SEVERITY_LABEL[aggregated.severity],
            "count": aggregated.count,
            "categories": sorted(categories_by_identity.get(aggregated.identity, ())),
            "example_url": aggregated.example_location,
            "solution": aggregated.remediation or "",
        }
        for aggregated in deduplicate(findings)
    ]


def derive_recommendations(findings: list[dict], severity_summary: dict) -> list[str]:
    """Build a short, prioritized list of recommendations."""
    if not findings:
        return ["No findings — no action required. Re-scan after significant changes."]

    recommendations: list[str] = []
    for severity in ("high", "medium", "low", "informational"):
        if severity_summary.get(severity, 0):
            recommendations.append(_SEVERITY_ADVICE[severity])

    # Call out the single most impactful finding by name.
    top = findings[0]
    recommendations.append(
        f"Start with '{top['name']}' ({top['risk']}), seen {top['count']} time(s) "
        f"in {', '.join(top['categories'])}."
    )
    return recommendations


def executive_summary(
    suite_name: str, application_summary: dict, findings: list[dict]
) -> dict[str, Any]:
    """Build the executive summary block (posture at a glance)."""
    risk = overall_risk(application_summary)
    total = application_summary.get("total", 0)
    if total == 0:
        headline = f"No security findings across {application_summary.get('endpoints', 0)} endpoint(s)."
    else:
        headline = (
            f"{application_summary.get('high', 0)} High / "
            f"{application_summary.get('medium', 0)} Medium finding(s) across "
            f"{application_summary.get('endpoints', 0)} endpoint(s) — overall risk: {risk}."
        )
    return {
        "suite_name": suite_name,
        "overall_risk": risk,
        "endpoints_scanned": application_summary.get("endpoints", 0),
        "endpoints_failed": application_summary.get("failed", 0),
        "total_findings": total,
        "distinct_findings": len(findings),
        "headline": headline,
    }

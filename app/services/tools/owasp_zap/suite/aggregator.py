"""Aggregate child scan_api results into an application-level report structure.

Pure and ZAP-free. Consumes the outcome of each child scan (its status + the
persisted ``ZapScanResult`` payload) and rolls it up into category summaries and
an application summary. It aggregates the child results — it does not re-derive
findings or run any new analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.findings import SEVERITY_ORDER
from app.services.tools.owasp_zap.suite.findings import (
    collect_findings,
    derive_recommendations,
    executive_summary,
)

# Severity keys used in the report payload, from the canonical domain vocabulary.
# Previously a hardcoded 4-tuple that could not represent a CRITICAL finding —
# any such finding was silently dropped from suite aggregation (Decision 0007).
_SEVERITIES = tuple(severity.value for severity in SEVERITY_ORDER)


@dataclass
class EndpointOutcome:
    """The result of one child scan_api operation within the suite."""

    category: str
    method: str
    url: str
    operation_id: str
    status: str  # "completed" / "failed" / other terminal status
    summary: dict = field(default_factory=dict)  # {total, high, medium, low, informational}
    error: Optional[str] = None
    alerts: list[dict] = field(default_factory=list)  # raw ZapAlert dicts (for SARIF)


def _empty_counts() -> dict:
    counts = {"total": 0}
    counts.update({sev: 0 for sev in _SEVERITIES})
    return counts


def _add_counts(target: dict, summary: dict) -> None:
    target["total"] += int(summary.get("total", 0) or 0)
    for sev in _SEVERITIES:
        target[sev] += int(summary.get(sev, 0) or 0)


def aggregate_application_report(
    suite_name: str,
    report_group: str,
    outcomes: list[EndpointOutcome],
) -> dict:
    """Roll *outcomes* up into an application report dict.

    Structure::

        {
          suite_name, report_group, generated_at,
          application_summary: {endpoints, completed, failed, total, high, ...},
          categories: [ {name, summary, endpoints: [...] }, ... ]
        }
    """
    categories: dict[str, dict] = {}
    app_counts = _empty_counts()
    completed = 0
    failed = 0

    for outcome in outcomes:
        category = categories.setdefault(
            outcome.category,
            {"name": outcome.category, "summary": _empty_counts(), "endpoints": []},
        )
        category["endpoints"].append(
            {
                "method": outcome.method,
                "url": outcome.url,
                "operation_id": outcome.operation_id,
                "status": outcome.status,
                "summary": outcome.summary or _empty_counts(),
                "error": outcome.error,
            }
        )
        _add_counts(category["summary"], outcome.summary or {})
        _add_counts(app_counts, outcome.summary or {})
        if outcome.status == "completed":
            completed += 1
        else:
            failed += 1

    application_summary: dict[str, Any] = {
        "endpoints": len(outcomes),
        "completed": completed,
        "failed": failed,
    }
    application_summary.update(app_counts)

    # Higher-level views derived from the same child data so every output format
    # (Markdown / JSON / SARIF) describes exactly the same findings.
    detailed_findings = collect_findings(outcomes)
    category_summary = [
        {
            "name": cat["name"],
            "endpoints": len(cat["endpoints"]),
            "summary": cat["summary"],
        }
        for cat in categories.values()
    ]

    return {
        "suite_name": suite_name,
        "report_group": report_group,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Ordered, report-friendly sections:
        "executive_summary": executive_summary(
            suite_name, application_summary, detailed_findings
        ),
        "scan_summary": {
            "endpoints": len(outcomes),
            "completed": completed,
            "failed": failed,
        },
        "severity_summary": dict(app_counts),
        "category_summary": category_summary,
        "recommendations": derive_recommendations(detailed_findings, app_counts),
        "detailed_findings": detailed_findings,
        # Retained for backward compatibility (per-endpoint detail + raw counts):
        "application_summary": application_summary,
        "categories": list(categories.values()),
    }

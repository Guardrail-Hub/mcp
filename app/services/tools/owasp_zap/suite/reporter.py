"""Render + persist aggregated suite reports in Markdown, JSON and SARIF.

Consumes the aggregated report dict (from :mod:`aggregator`) plus the raw child
alerts, and writes three artifacts next to the per-scan reports. All three
formats describe the same aggregated findings — Markdown for humans, JSON for
machines/dashboards, SARIF for code-scanning tools (GitHub, IDEs). It aggregates
existing child findings; it never invents a new finding model.
"""

import json
from pathlib import Path
from typing import Optional

from app.constants.paths import ReportPaths
from app.core.config import settings
from app.core.mcp_logger import MCPLogger
from app.services.tools.owasp_zap.suite.aggregator import EndpointOutcome

logger = MCPLogger("OwaspZapSuiteReporter")

# ZAP risk → SARIF level.
_SARIF_LEVEL = {
    "high": "error",
    "medium": "warning",
    "low": "note",
    "informational": "none",
}


class SuiteReporter:
    """Render aggregated reports and persist them, returning public URLs."""

    # ── JSON ─────────────────────────────────────────────────────────────

    def to_json(self, report: dict) -> str:
        """The full aggregated report — the canonical machine-readable form."""
        return json.dumps(report, indent=2, ensure_ascii=False)

    # ── Markdown ─────────────────────────────────────────────────────────

    def to_markdown(self, report: dict) -> str:
        exec_s = report.get("executive_summary", {})
        sev = report.get("severity_summary", {})
        scan = report.get("scan_summary", {})
        lines = [
            f"# Application Security Report — {report['suite_name']}",
            "",
            f"Report group: `{report['report_group']}`  ",
            f"Generated: {report['generated_at']}",
            "",
            "## Executive Summary",
            "",
            f"- Overall risk: **{exec_s.get('overall_risk', 'Unknown')}**",
            f"- {exec_s.get('headline', '')}",
            f"- Endpoints scanned: {exec_s.get('endpoints_scanned', 0)} "
            f"(failed: {exec_s.get('endpoints_failed', 0)})",
            f"- Distinct findings: {exec_s.get('distinct_findings', 0)}",
            "",
            "## Scan Summary",
            "",
            f"- Endpoints: **{scan.get('endpoints', 0)}** "
            f"(completed: {scan.get('completed', 0)}, failed: {scan.get('failed', 0)})",
            "",
            "## Severity Summary",
            "",
            "| High | Medium | Low | Informational | Total |",
            "| ---: | ---: | ---: | ---: | ---: |",
            f"| {sev.get('high', 0)} | {sev.get('medium', 0)} | {sev.get('low', 0)} "
            f"| {sev.get('informational', 0)} | {sev.get('total', 0)} |",
            "",
            "## Category Summary",
            "",
            "| Category | Endpoints | High | Medium | Low | Info |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for cat in report.get("category_summary", []):
            cs = cat["summary"]
            lines.append(
                f"| {cat['name']} | {cat['endpoints']} | {cs['high']} | "
                f"{cs['medium']} | {cs['low']} | {cs['informational']} |"
            )
        lines += ["", "## Recommendations", ""]
        for rec in report.get("recommendations", []):
            lines.append(f"- {rec}")
        lines += ["", "## Detailed Findings", ""]
        findings = report.get("detailed_findings", [])
        if not findings:
            lines.append("_No findings._")
        else:
            lines.append("| Risk | Finding | Count | Categories | Example |")
            lines.append("| --- | --- | ---: | --- | --- |")
            for f in findings:
                lines.append(
                    f"| {f['risk']} | {f['name']} | {f['count']} | "
                    f"{', '.join(f['categories'])} | {f['example_url']} |"
                )
        lines += ["", "## Endpoints by Category", ""]
        for cat in report.get("categories", []):
            lines.append(f"### {cat['name']}")
            lines.append("")
            lines.append("| Method | Endpoint | Status | High | Medium | Low | Info |")
            lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: |")
            for ep in cat["endpoints"]:
                es = ep["summary"]
                status = ep["status"] if not ep.get("error") else "failed"
                lines.append(
                    f"| {ep['method']} | {ep['url']} | {status} | {es['high']} | "
                    f"{es['medium']} | {es['low']} | {es['informational']} |"
                )
            lines.append("")
        return "\n".join(lines)

    # ── SARIF ────────────────────────────────────────────────────────────

    def to_sarif(self, outcomes: list[EndpointOutcome]) -> str:
        rules: dict[str, dict] = {}
        results: list[dict] = []
        for outcome in outcomes:
            for alert in outcome.alerts:
                rule_id = str(alert.get("alert_ref") or alert.get("name") or "zap")
                if rule_id not in rules:
                    rules[rule_id] = {
                        "id": rule_id,
                        "name": alert.get("name", rule_id),
                        "shortDescription": {"text": alert.get("name", rule_id)},
                        "helpUri": "https://www.zaproxy.org/docs/alerts/",
                        "help": {"text": alert.get("solution") or "See OWASP ZAP guidance."},
                    }
                risk = str(alert.get("risk", "")).lower()
                results.append(
                    {
                        "ruleId": rule_id,
                        "level": _SARIF_LEVEL.get(risk, "none"),
                        "message": {
                            "text": f"{alert.get('name', '')}: {alert.get('description', '')}".strip(": ")
                        },
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {
                                        "uri": alert.get("url") or outcome.url
                                    }
                                }
                            }
                        ],
                        "properties": {"category": outcome.category, "risk": alert.get("risk")},
                    }
                )
        return json.dumps(
            {
                "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {
                            "driver": {
                                "name": "OWASP ZAP",
                                "informationUri": "https://www.zaproxy.org/",
                                "rules": list(rules.values()),
                            }
                        },
                        "results": results,
                    }
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    # ── Persistence ──────────────────────────────────────────────────────

    def save(
        self, operation_id: str, report: dict, outcomes: list[EndpointOutcome]
    ) -> dict[str, Optional[str]]:
        """Write MD/JSON/SARIF artifacts and return their public URLs."""
        base = settings.mcp_server_public_url.rstrip("/")
        report_dir = Path(ReportPaths.ZAP_DETAIL_DIR)
        urls: dict[str, Optional[str]] = {
            "view_report": None,
            "export_data": None,
            "sarif": None,
        }
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "view_report": (f"{operation_id}-suite.md", self.to_markdown(report)),
                "export_data": (f"{operation_id}-suite.json", self.to_json(report)),
                "sarif": (f"{operation_id}-suite.sarif.json", self.to_sarif(outcomes)),
            }
            for key, (filename, content) in artifacts.items():
                (report_dir / filename).write_text(content, encoding="utf-8")
                urls[key] = f"{base}/public/report/owasp-zap/detail-report/{filename}"
            logger.info("Suite reports saved for %s", operation_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to save suite reports for %s: %s", operation_id, exc)
        return urls

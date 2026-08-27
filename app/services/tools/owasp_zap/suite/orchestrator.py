"""SuiteOrchestrator — fan-out / fan-in over existing scan_api operations.

This is the heart of scan_api_suite and it never scans anything itself. Given a
suite request it:

1. expands each category's OpenAPI spec into endpoints (fan-out plan);
2. submits one child ``scan_api`` operation per endpoint (persisted QUEUED);
3. lets the existing generic Dispatcher schedule those children onto workers,
   in parallel — the suite holds NO worker while they run;
4. polls child statuses until all are terminal (fan-in);
5. aggregates the child results into application/category reports.

Every collaborator is injected (``scan_submitter``, ``operation_getter``,
``reporter``, ``sleep``) so the orchestration is fully unit-testable without ZAP,
a database, or the worker pool.
"""

import time
from typing import Any, Callable, Optional

from app.core.mcp_logger import MCPLogger
from app.schemas.tools.owasp_zap.api_scan import ZapApiScanRequest
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.owasp_zap.scan_api_suite import (
    ApiCategory,
    ZapApiSuiteScanRequest,
)
from app.services.tools.owasp_zap.suite.aggregator import (
    EndpointOutcome,
    aggregate_application_report,
)
from app.schemas.tools.owasp_zap.openapi_spec import extract_endpoints
from app.services.tools.owasp_zap.suite.reporter import SuiteReporter

_TERMINAL = {OperationPhase.COMPLETED, OperationPhase.FAILED, OperationPhase.CANCELLED}

# Callable signatures (documentation): a child submitter and a status reader.
ScanSubmitter = Callable[[ZapApiScanRequest, str], str]
OperationGetter = Callable[[str], Any]


class SuiteOrchestrator:
    """Orchestrates child scan_api operations for one suite operation."""

    def __init__(
        self,
        operation_service: Any,
        *,
        scan_submitter: ScanSubmitter,
        operation_getter: OperationGetter,
        reporter: Optional[SuiteReporter] = None,
        poll_interval_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        logger: Optional[Any] = None,
    ) -> None:
        self._op = operation_service
        self._scan_submitter = scan_submitter
        self._operation_getter = operation_getter
        self._reporter = reporter or SuiteReporter()
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep
        self._logger = logger or MCPLogger("OwaspZapSuiteOrchestrator")

    # ── Entry point ──────────────────────────────────────────────────────

    def run(self, operation_id: str, request: ZapApiSuiteScanRequest) -> None:
        """Fan out child scans, wait, aggregate, and complete the suite op."""
        try:
            self._op.report_progress(operation_id, stage="planning", progress=5)
            plan = self._build_plan(request)
            if not plan:
                self._op.fail(
                    operation_id,
                    "No endpoints were found in any category's OpenAPI specification.",
                )
                return

            children: list[dict] = []
            for category_name, method, url, child_request in plan:
                child_id = self._scan_submitter(child_request, operation_id)
                children.append(
                    {
                        "category": category_name,
                        "method": method,
                        "url": url,
                        "operation_id": child_id,
                    }
                )

            self._logger.info(
                "Suite '%s' fanned out %d child scan(s) across %d category(ies)",
                operation_id, len(children), len(request.categories),
            )
            self._op.report_progress(
                operation_id,
                stage="scanning",
                progress=15,
                message=f"{len(children)} endpoint scan(s) queued",
            )
            self._await_children(operation_id, children)

            outcomes = self._collect(children)
            report = aggregate_application_report(
                request.suite_name, request.report_group, outcomes
            )
            report["reports"] = self._reporter.save(operation_id, report, outcomes)

            self._logger.info(
                "Suite '%s' aggregated %d finding(s); overall risk: %s",
                operation_id,
                report["severity_summary"]["total"],
                report["executive_summary"]["overall_risk"],
            )
            self._op.complete(
                operation_id,
                result=report,
                findings=self._findings(report["application_summary"]),
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error("Suite '%s' failed: %s", operation_id, exc)
            self._op.fail(operation_id, str(exc))

    # ── Fan-out planning ─────────────────────────────────────────────────

    def _build_plan(self, request: ZapApiSuiteScanRequest) -> list[tuple]:
        plan: list[tuple] = []
        for category in request.categories:
            methods = [m.value for m in category.methods] if category.methods else None
            endpoints = extract_endpoints(category.openapi_spec, category.base_url, methods)
            for endpoint in endpoints:
                child_request = self._child_request(request, category, endpoint)
                plan.append((category.name, endpoint.method, endpoint.url, child_request))
        return plan

    @staticmethod
    def _child_request(
        request: ZapApiSuiteScanRequest, category: ApiCategory, endpoint
    ) -> ZapApiScanRequest:
        """Build a normal scan_api request for one endpoint (reuses scan_api)."""
        data: dict[str, Any] = {
            "report_group": f"{request.suite_name}/{category.name}",
            "target_url": endpoint.url,
            "method": endpoint.method,
            "scan_mode": request.scan_mode.value,
        }
        defaults = category.defaults
        if defaults is not None:
            if defaults.headers:
                data["headers"] = defaults.headers
            if defaults.token:
                data["token"] = defaults.token
                data["token_type"] = defaults.token_type.value
                data["token_header_name"] = defaults.token_header_name
                if defaults.token_prefix:
                    data["token_prefix"] = defaults.token_prefix
            if defaults.cookie:
                data["cookie"] = defaults.cookie
        return ZapApiScanRequest.model_validate(data)

    # ── Fan-in ───────────────────────────────────────────────────────────

    def _await_children(self, operation_id: str, children: list[dict]) -> None:
        pending = {child["operation_id"] for child in children}
        total = len(children)
        while pending:
            still_pending = set()
            for child_id in pending:
                record = self._operation_getter(child_id)
                if record is None or record.status in _TERMINAL:
                    continue  # terminal (or vanished) → no longer pending
                still_pending.add(child_id)
            pending = still_pending
            if pending:
                done = total - len(pending)
                self._op.report_progress(
                    operation_id,
                    stage="scanning",
                    progress=15 + int(75 * done / total),
                    message=f"{done}/{total} endpoint scan(s) complete",
                )
                self._sleep(self._poll_interval)

    def _collect(self, children: list[dict]) -> list[EndpointOutcome]:
        outcomes: list[EndpointOutcome] = []
        for child in children:
            record = self._operation_getter(child["operation_id"])
            raw_status = getattr(record, "status", None) if record else None
            status_value = getattr(raw_status, "value", raw_status) or "failed"
            result = (
                record.result
                if record is not None and isinstance(record.result, dict)
                else {}
            )
            is_completed = status_value == OperationPhase.COMPLETED.value
            outcomes.append(
                EndpointOutcome(
                    category=child["category"],
                    method=child["method"],
                    url=child["url"],
                    operation_id=child["operation_id"],
                    status="completed" if is_completed else "failed",
                    summary=result.get("summary") or {},
                    alerts=result.get("alerts") or [],
                    error=None if is_completed else getattr(record, "error", "scan failed"),
                )
            )
        return outcomes

    @staticmethod
    def _findings(application_summary: dict) -> dict:
        return {
            "critical": 0,
            "high": application_summary.get("high", 0),
            "medium": application_summary.get("medium", 0),
            "low": application_summary.get("low", 0),
            "informational": application_summary.get("informational", 0),
        }

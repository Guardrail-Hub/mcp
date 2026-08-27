"""Suite orchestration engine for scan_api_suite.

Pure, ZAP-free building blocks (OpenAPI expansion, result aggregation, report
rendering) plus the :class:`SuiteOrchestrator` that fans out child scan_api
operations and fans their results back in. The suite NEVER scans and NEVER holds
a worker — it only orchestrates existing scan_api executions.
"""

from app.services.tools.owasp_zap.suite.aggregator import (
    EndpointOutcome,
    aggregate_application_report,
)
from app.schemas.tools.owasp_zap.openapi_spec import Endpoint, extract_endpoints
from app.services.tools.owasp_zap.suite.orchestrator import SuiteOrchestrator
from app.services.tools.owasp_zap.suite.reporter import SuiteReporter

__all__ = [
    "Endpoint",
    "EndpointOutcome",
    "SuiteOrchestrator",
    "SuiteReporter",
    "aggregate_application_report",
    "extract_endpoints",
]

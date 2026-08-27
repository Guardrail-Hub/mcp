"""
API Scenario scan service.

A scenario runs an ordered workflow of API requests (Login → … → Logout) through
the ZAP proxy, propagating variables / cookies / JWT between steps, and then runs
the OWASP ZAP active scan over the authenticated endpoints. It REUSES the
existing scanning primitives (``ZapClient`` + ``ZapApiScanHelpers``) rather than
duplicating any scan_api scanning logic — the scenario only adds the workflow
replay + context propagation in front of the standard scan.

Queueing model mirrors scan_api exactly: ``init_scenario_scan`` persists a
``QUEUED`` operation (the full request in ``metadata`` so it can be resumed /
replayed after a restart) and returns immediately. The generic Dispatcher later
resolves this operation's handler from the registry and calls
:meth:`run_assigned` on an assigned worker.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Optional

import httpx

from app.constants.batch import BatchType
from app.core.config import settings
from app.core.events.dispatcher import InProcessEventDispatcher
from app.core.exception import InvalidScanRequestError, ScanSubmissionError
from app.core.mcp_logger import MCPLogger
from app.dao.operation_dao import get_operation_dao
from app.helpers.tools.owasp_zap_helpers import ZapApiScanHelpers
from app.integrations.owasp_zap.runtime import pool_manager as default_pool_manager
from app.schemas.tools.owasp_zap.api_scan import ZapApiScanRequest
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.owasp_zap.scan_api_scenario import (
    ScenarioStep,
    ZapApiScenarioScanRequest,
)
from app.schemas.tools.owasp_zap.scan_result import ZapScanReports, ZapScanResult
from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo
from app.services.operations.operation_service import OperationService
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType
from app.services.tools.owasp_zap.scenario.context import (
    ScenarioContext,
    ScenarioStepError,
    resolve_execution_order,
)

# Own thread pool, matching api_scanner's decoupling rationale: real concurrency
# is bounded by the number of registered ZAP workers, not this executor.
executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="zap-scenario-worker")


class ZapApiScenarioService:
    """Singleton service that runs OWASP ZAP API scenario scans on a worker."""

    _instance: Optional["ZapApiScenarioService"] = None
    _instance_lock: Lock = Lock()

    def __new__(cls, *args, **kwargs) -> "ZapApiScenarioService":
        # Mirror ZapApiScanService: tolerate constructor kwargs in __new__ and
        # use double-checked locking so startup can inject the shared service.
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, operation_service: Optional[OperationService] = None) -> None:
        if getattr(self, "_initialized", False):
            if operation_service is not None:
                self.operation_service = operation_service
            return

        self.app_logger = MCPLogger("OwaspZapApiScenarioScanService")
        self.pool_manager = default_pool_manager
        self.zap_helpers = ZapApiScanHelpers()
        self.operation_dao = get_operation_dao()
        self.operation_service = operation_service or OperationService(
            self.operation_dao, InProcessEventDispatcher()
        )
        self._initialized = True

    # ------------------------------------------------------------------
    # Submit (persist QUEUED)
    # ------------------------------------------------------------------

    def init_scenario_scan(self, request: ZapApiScenarioScanRequest) -> dict:
        """Validate *request* and persist it as a ``QUEUED`` scenario operation."""
        if not (request.report_group or "").strip():
            raise InvalidScanRequestError("Report group is required")
        if not request.steps:
            raise InvalidScanRequestError("At least one step is required")

        try:
            op_id = str(uuid.uuid4())
            first = request.steps[0]
            self.operation_service.create(
                batch_type=BatchType.BATCH_SCAN,
                operation_id=op_id,
                metadata={
                    "operation_type": ZapOperationType.API_SCENARIO.value,
                    "name": "Owasp Zap scenario check",
                    "target_url": first.url,
                    "method": first.method.value,
                    "request": request.model_dump(mode="json", by_alias=True),
                },
            )
            self.app_logger.info(
                f"Queued API scenario scan ({len(request.steps)} steps) op_id: {op_id}"
            )
            return {
                "operation_id": op_id,
                "status": OperationPhase.QUEUED,
                "message": (
                    f"Zap API scenario scan queued with operation_id: {op_id}. It will "
                    f"start as soon as a ZAP worker is available. Check status at "
                    f"api/history/get-result with param operation_id: {op_id}"
                ),
            }
        except InvalidScanRequestError:
            raise
        except Exception as e:
            raise ScanSubmissionError(
                f"Error initializing scenario scan:\n{str(e)}"
            ) from e

    # ------------------------------------------------------------------
    # Execute (called by the handler on an assigned worker)
    # ------------------------------------------------------------------

    def run_assigned(
        self,
        operation_id: str,
        request: ZapApiScenarioScanRequest,
        worker: ZapWorkerInfo,
    ) -> None:
        """Submit the scenario run to the thread pool (never blocks the Dispatcher)."""
        executor.submit(self._run_scenario, operation_id, request, worker)

    def _run_scenario(
        self,
        operation_id: str,
        request: ZapApiScenarioScanRequest,
        worker: ZapWorkerInfo,
    ) -> None:
        start_time = time.time()
        try:
            zap_client = self.pool_manager.get_client(worker)
            zap_api = zap_client.zap_api
            self.app_logger.info(
                f"[{worker.worker_id}] Start API scenario scan: {operation_id}"
            )

            zap_client.require_healthy()
            self.operation_service.start(operation_id)
            self.operation_service.report_progress(
                operation_id, stage="initializing", progress=5, worker_id=worker.worker_id
            )
            zap_client.new_session(f"scenario-{operation_id[:8]}")

            context = ScenarioContext()
            ordered = resolve_execution_order(request.steps)

            # ── Phase 1: replay the workflow, building authenticated context ──
            total = len(ordered)
            for index, step in enumerate(ordered):
                resolved = context.resolve_step(step)
                # Pre-flight each step's target from the assigned worker, so an
                # unreachable host fails fast with actionable guidance rather than
                # a later url_not_found from the active-scan phase.
                zap_client.check_target_reachable(resolved.url)
                headers = self.zap_helpers.build_request_headers(resolved)
                response_json, set_cookies = self._send_step(
                    zap_client.proxy_url, resolved, headers
                )
                context.capture(step, response_json, set_cookies)
                self.operation_service.report_progress(
                    operation_id,
                    stage=f"replay: {step.name}",
                    progress=5 + int(40 * (index + 1) / total),
                    worker_id=worker.worker_id,
                )

            zap_client.wait_for_passive_scan(
                timeout_seconds=settings.zap_passive_scan_timeout_seconds
            )

            # ── Phase 2: active-scan the authenticated endpoints ──────────────
            scan_targets = [s for s in ordered if s.scan] or ordered
            context_name = f"scenario-scan-{operation_id[:8]}"
            context_id = zap_client.create_context(context_name)
            for pattern in {self.zap_helpers.scope_pattern(s.url) for s in scan_targets}:
                zap_client.include_in_context(context_name, pattern)

            auth_request = self._auth_request(request, context, scan_targets[0].url)
            self.zap_helpers.setup_auth_replacers(
                zap_api, auth_request, self.zap_helpers.build_request_headers(auth_request)
            )

            deduped: dict[tuple, Any] = {}
            for index, step in enumerate(scan_targets):
                resolved = context.resolve_step(step)
                body_bytes, _ = self.zap_helpers.serialize_body(resolved)
                postdata = body_bytes.decode() if body_bytes else None
                self.operation_service.report_progress(
                    operation_id,
                    stage=f"active scan: {step.name}",
                    progress=55 + int(30 * (index + 1) / len(scan_targets)),
                    worker_id=worker.worker_id,
                )
                ascan_id = zap_client.run_active_scan(
                    url=resolved.url,
                    context_id=context_id,
                    recurse=False,
                    method=resolved.method.value,
                    postdata=postdata,
                )
                zap_client.wait_for_active_scan(
                    ascan_id,
                    stall_timeout_seconds=settings.zap_scan_stall_timeout_seconds,
                    unresponsive_timeout_seconds=settings.zap_scan_unresponsive_timeout_seconds,
                )
                for raw in zap_client.get_alerts(base_url=resolved.url):
                    alert = self.zap_helpers.convert_raw_alert(raw)
                    deduped[(alert.alert_ref, alert.url)] = alert

            zap_client.remove_context(context_name)
            alerts = list(deduped.values())

            self.operation_service.report_progress(
                operation_id, stage="report generation", progress=90, worker_id=worker.worker_id
            )
            view_report, export_data = self.zap_helpers.save_reports(zap_client, operation_id)
            summary = self.zap_helpers.build_summary(alerts)

            self.operation_service.complete(
                operation_id,
                result=ZapScanResult(
                    operation_id=operation_id,
                    report_group=request.report_group,
                    status="completed",
                    target_url=scan_targets[0].url,
                    duration_seconds=round(time.time() - start_time, 2),
                    alerts=alerts,
                    summary=summary,
                    tls_result=self.zap_helpers.check_tls(scan_targets[0].url),
                    reports=ZapScanReports(view_report=view_report, export_data=export_data),
                ).model_dump(by_alias=True),
                findings=self.zap_helpers.findings_from_summary(summary),
            )

        except ScenarioStepError as exc:
            # Failure reporting: attribute the failure to the offending step.
            self.app_logger.error(
                f"Scenario '{operation_id}' failed at step '{exc.step_name}': {exc}"
            )
            self.operation_service.fail(operation_id, str(exc))
        except Exception as exc:  # pylint: disable=broad-except
            self.app_logger.error(f"Error in scenario scan {operation_id}: {exc}")
            self.operation_service.fail(operation_id, str(exc))
        finally:
            self.pool_manager.release_worker(worker.worker_id, operation_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_step(
        self, proxy_url: str, step: ScenarioStep, headers: dict[str, str]
    ) -> tuple[Any, dict[str, str]]:
        """Send one step through the ZAP proxy and return ``(json, set_cookies)``.

        Unlike the fire-and-forget ``ZapApiScanHelpers.send_through_proxy`` (which
        the single-target scan uses), a scenario needs the response to extract
        variables/tokens/cookies — so a step failure is raised (not swallowed) as
        a :class:`ScenarioStepError` for failure reporting.
        """
        body_bytes, content_type = self.zap_helpers.serialize_body(step)
        if content_type and "Content-Type" not in headers:
            headers["Content-Type"] = content_type
        try:
            with httpx.Client(
                proxy=proxy_url,
                verify=False,
                timeout=settings.zap_request_timeout_seconds,
            ) as http:
                response = http.request(
                    method=step.method.value,
                    url=step.url,
                    headers=headers,
                    content=body_bytes,
                )
        except Exception as exc:  # pylint: disable=broad-except
            raise ScenarioStepError(step.name, f"request failed: {exc}") from exc

        try:
            parsed = response.json()
        except Exception:  # noqa: BLE001 - non-JSON responses are fine
            parsed = None
        return parsed, dict(response.cookies)

    @staticmethod
    def _auth_request(
        request: ZapApiScenarioScanRequest,
        context: ScenarioContext,
        target_url: str,
    ) -> ZapApiScanRequest:
        """Build a synthetic single-target request carrying the accumulated auth.

        Reused only to drive ``setup_auth_replacers`` so ZAP applies the captured
        token/cookie to every request it issues during the active scan.
        """
        cookie = "; ".join(f"{n}={v}" for n, v in context.cookies.items()) or None
        return ZapApiScanRequest.model_validate(
            {
                "report_group": request.report_group,
                "target_url": target_url,
                "method": "GET",
                "token": context.token,
                "token_type": context.token_type.value,
                "token_header_name": context.token_header_name,
                "token_prefix": context.token_prefix,
                "cookie": cookie,
                "scan_mode": request.scan_mode.value,
            }
        )

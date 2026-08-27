"""
API Suite orchestration service.

``scan_api_suite`` is NOT a scanner — it is an orchestration operation. It expands
multiple OpenAPI specifications into child ``scan_api`` operations, lets the
existing generic Dispatcher schedule those children across the worker pool (in
parallel), waits for them to finish, and aggregates their results into
application-level reports (Markdown / JSON / SARIF).

Because the suite is registered with ``ExecutionStrategy.FAN_OUT``, the generic
Dispatcher runs it on its orchestration executor and NEVER assigns it a ZAP
worker — workers only ever execute the child ``scan_api`` operations.
"""

import uuid
from threading import Lock
from typing import Optional

from app.constants.batch import BatchType
from app.core.config import settings
from app.core.events.dispatcher import InProcessEventDispatcher
from app.core.exception import InvalidScanRequestError, ScanSubmissionError
from app.core.mcp_logger import MCPLogger
from app.dao.operation_dao import get_operation_dao
from app.schemas.tools.owasp_zap.api_scan import ZapApiScanRequest
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.owasp_zap.scan_api_suite import ZapApiSuiteScanRequest
from app.services.operations.operation_service import OperationService
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType
from app.services.tools.owasp_zap.suite.orchestrator import SuiteOrchestrator


class ZapApiSuiteService:
    """Singleton orchestration service for OWASP ZAP API suite scans."""

    _instance: Optional["ZapApiSuiteService"] = None
    _instance_lock: Lock = Lock()

    def __new__(cls, *args, **kwargs) -> "ZapApiSuiteService":
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

        self.app_logger = MCPLogger("OwaspZapApiSuiteScanService")
        self.operation_dao = get_operation_dao()
        self.operation_service = operation_service or OperationService(
            self.operation_dao, InProcessEventDispatcher()
        )
        self._initialized = True

    # ------------------------------------------------------------------
    # Submit (persist QUEUED)
    # ------------------------------------------------------------------

    def init_suite_scan(self, request: ZapApiSuiteScanRequest) -> dict:
        """Validate *request* and persist it as a ``QUEUED`` suite operation."""
        if not (request.report_group or "").strip():
            raise InvalidScanRequestError("Report group is required")
        if not request.categories:
            raise InvalidScanRequestError("At least one category is required")

        try:
            op_id = str(uuid.uuid4())
            self.operation_service.create(
                batch_type=BatchType.SUITE_SCAN,
                operation_id=op_id,
                metadata={
                    "operation_type": ZapOperationType.API_SUITE.value,
                    "name": f"Owasp Zap suite: {request.suite_name}",
                    "suite_name": request.suite_name,
                    "request": request.model_dump(mode="json", by_alias=True),
                },
            )
            self.app_logger.info(
                f"Queued API suite scan '{request.suite_name}' "
                f"({len(request.categories)} categories) op_id: {op_id}"
            )
            return {
                "operation_id": op_id,
                "status": OperationPhase.QUEUED,
                "message": (
                    f"Zap API suite scan queued with operation_id: {op_id}. Child "
                    f"scans will run in parallel as workers become available. Check "
                    f"status at api/history/get-result with param operation_id: {op_id}"
                ),
            }
        except InvalidScanRequestError:
            raise
        except Exception as e:
            raise ScanSubmissionError(
                f"Error initializing suite scan:\n{str(e)}"
            ) from e

    # ------------------------------------------------------------------
    # Execute (called by the handler; orchestration — no worker)
    # ------------------------------------------------------------------

    def run(self, operation_id: str, request: ZapApiSuiteScanRequest) -> None:
        """Run the fan-out/fan-in orchestration for a suite operation.

        Called by :class:`ApiSuiteHandler` on the Dispatcher's orchestration
        executor with ``worker=None``. All real scanning is delegated to child
        ``scan_api`` operations via :meth:`_submit_child`.
        """
        orchestrator = SuiteOrchestrator(
            self.operation_service,
            scan_submitter=self._submit_child,
            operation_getter=self.operation_service.get,
            poll_interval_seconds=settings.zap_queue_poll_interval_seconds,
            logger=self.app_logger,
        )
        orchestrator.run(operation_id, request)

    def _submit_child(
        self, child_request: ZapApiScanRequest, parent_operation_id: str
    ) -> str:
        """Enqueue one child ``scan_api`` operation via the existing scan service.

        Reuses ``ZapApiScanService.init_api_scan`` (the standard scan_api submit
        path) so the suite never duplicates scan submission logic. Imported
        lazily to avoid importing the zaproxy-dependent scanner at module load.
        """
        from app.services.tools.owasp_zap.api_scanner import (  # noqa: PLC0415
            ZapApiScanService,
        )

        result = ZapApiScanService(
            operation_service=self.operation_service
        ).init_api_scan(child_request, parent_operation_id=parent_operation_id)
        return result["operation_id"]

"""``scan_api`` handler — a thin adapter over the existing ``ZapApiScanService``.

This does NOT reimplement or duplicate any scan logic. ``ZapApiScanService`` is
unchanged; the handler simply satisfies the generic
:class:`ZapOperationHandler` contract by delegating ``execute`` to the service's
existing ``run_assigned_scan``. As a result ``scan_api`` behaviour is identical
to before the generic-execution refactor — the Dispatcher just reaches it
through the registry instead of a direct import.

``scan_api`` is a worker-bound, single-scan tool, so its
:class:`ExecutionStrategy` is ``SEQUENTIAL`` and ``worker`` is always a bound
:class:`ZapWorkerInfo` (never ``None``).
"""

from typing import Optional

from app.schemas.tools.owasp_zap.api_scan import ZapApiScanRequest
from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo
from app.services.operations.operation_service import OperationService
from app.services.tools.owasp_zap.api_scanner import ZapApiScanService
from app.services.tools.owasp_zap.execution.execution_strategy import ExecutionStrategy
from app.services.tools.owasp_zap.execution.handler import ZapOperation
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType


class ApiScanHandler:
    """Adapts ``ZapApiScanService`` to the generic ``ZapOperationHandler``."""

    def __init__(self, scan_service: ZapApiScanService) -> None:
        self._scan_service = scan_service

    def execute(
        self,
        operation_id: str,
        request: ZapApiScanRequest,
        worker: Optional[ZapWorkerInfo],
    ) -> None:
        """Run an already-assigned API scan on *worker*.

        ``worker`` is guaranteed non-``None`` for this worker-bound strategy;
        the Dispatcher only calls this after the Pool Manager has bound the
        worker to *operation_id*. Delegates verbatim to the existing service,
        which submits the matching worker function to its thread pool and
        releases the worker when done.
        """
        if worker is None:  # defensive: should never happen for a worker-bound op
            raise ValueError("ApiScanHandler.execute requires an assigned worker")
        self._scan_service.run_assigned_scan(operation_id, request, worker)


def build_api_scan_operation(operation_service: OperationService) -> ZapOperation:
    """Build the registrable :class:`ZapOperation` for ``scan_api``.

    Uses ``ZapApiScanRequest`` as the request model — exactly the type the
    legacy ``ZapApiScanService.request_from_metadata`` reconstructed — so
    deserialization is byte-for-byte equivalent to the previous dispatch path.
    """
    scan_service = ZapApiScanService(operation_service=operation_service)
    return ZapOperation(
        operation_type=ZapOperationType.API_SCAN,
        execution_strategy=ExecutionStrategy.SEQUENTIAL,
        request_model=ZapApiScanRequest,
        handler=ApiScanHandler(scan_service),
    )

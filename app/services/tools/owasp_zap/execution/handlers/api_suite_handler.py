"""``scan_api_suite`` handler — adapts ZapApiSuiteService to the generic contract.

A suite is an orchestration operation, registered with
``ExecutionStrategy.FAN_OUT``. The generic Dispatcher therefore runs it on its
orchestration executor with ``worker=None`` and never assigns it a ZAP worker —
workers only ever run the child ``scan_api`` operations the suite creates.
"""

from typing import Optional

from app.schemas.tools.owasp_zap.scan_api_suite import ZapApiSuiteScanRequest
from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo
from app.services.operations.operation_service import OperationService
from app.services.tools.owasp_zap.execution.execution_strategy import ExecutionStrategy
from app.services.tools.owasp_zap.execution.handler import ZapOperation
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType
from app.services.tools.owasp_zap.scan_api_suite import ZapApiSuiteService


class ApiSuiteHandler:
    """Adapts ``ZapApiSuiteService`` to the generic ``ZapOperationHandler``."""

    def __init__(self, service: ZapApiSuiteService) -> None:
        self._service = service

    def execute(
        self,
        operation_id: str,
        request: ZapApiSuiteScanRequest,
        worker: Optional[ZapWorkerInfo],
    ) -> None:
        # worker is always None for an orchestration (FAN_OUT) strategy; the
        # suite never occupies a worker. Runs on the Dispatcher's orchestration
        # executor, so this call may block for the suite's lifetime (intended).
        self._service.run(operation_id, request)


def build_api_suite_operation(operation_service: OperationService) -> ZapOperation:
    """Build the registrable :class:`ZapOperation` for ``scan_api_suite``."""
    service = ZapApiSuiteService(operation_service=operation_service)
    return ZapOperation(
        operation_type=ZapOperationType.API_SUITE,
        execution_strategy=ExecutionStrategy.FAN_OUT,
        request_model=ZapApiSuiteScanRequest,
        handler=ApiSuiteHandler(service),
    )

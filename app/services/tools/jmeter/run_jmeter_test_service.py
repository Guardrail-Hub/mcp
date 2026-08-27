"""JMeter submission service — validate a request and enqueue an operation.

This is the *whole* submit path, and it is deliberately complete: submission is
not execution. The endpoint validates, persists one ``QUEUED`` row, and returns
an ``operation_id``. Execution is the Dispatcher's and Runtime's job, and the
row itself is the queue entry that survives a restart.

The row's ``batch_type`` is ``BatchType.JMETER_TEST``, which is what keeps it in
the JMeter queue lane and invisible to the ZAP dispatcher (ADR-0011).
"""

import uuid
from typing import Optional

from app.constants.batch import BatchType
from app.core.exception import ScanSubmissionError
from app.core.mcp_logger import MCPLogger
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.jmeter.run_jmeter_test import (
    JMeterTestRequest,
    JMeterTestSubmissionResponse,
)
from app.services.operations.operation_service import OperationService


class JMeterTestService:
    """Enqueues JMeter load tests. Runs nothing."""

    def __init__(
        self,
        operation_service: Optional[OperationService] = None,
        logger: Optional[MCPLogger] = None,
    ) -> None:
        """
        Args:
            operation_service: Owns operation creation, lifecycle and events.
                When not injected, an unsubscribed in-process bus is used, so
                this behaves as persist-and-publish-to-nobody — the same
                fallback ``ZapApiScanService`` uses. Startup injects the shared,
                event-publishing instance.
            logger: Optional logger; one is created if omitted.
        """
        if operation_service is None:
            # Imported lazily so constructing this service does not select a
            # database provider at module import time.
            from app.core.events.dispatcher import (  # noqa: PLC0415
                InProcessEventDispatcher,
            )
            from app.dao.operation_dao import get_operation_dao  # noqa: PLC0415

            operation_service = OperationService(
                get_operation_dao(), InProcessEventDispatcher()
            )
        self._operation_service = operation_service
        self._logger = logger or MCPLogger("JMeterTestService")

    def init_run_jmeter_test(
        self, request: JMeterTestRequest
    ) -> JMeterTestSubmissionResponse:
        """Persist *request* as a ``QUEUED`` JMeter operation.

        Named for the ``run_jmeter`` tool it initiates, matching the submit-path
        convention the ZAP services follow (``init_api_scan``,
        ``init_scenario_scan``, ``init_suite_scan``): ``init_*`` starts an
        asynchronous operation and returns a receipt, never a result.

        The full request is stored under ``metadata["request"]`` so the Runtime
        can reconstruct it later — including after a server restart, since this
        row is the queue entry.

        ``metadata["target_url"]`` and ``metadata["method"]`` are also stored
        flat because the notification layer resolves the user-facing target line
        from those keys (``app.bootstrap._format_scan_target``).

        Args:
            request: An already-validated load-test request. Field-level
                validation, including the Guardrail G4 explicit-target rule,
                happens in :class:`JMeterTestRequest` at the boundary.

        Returns:
            The submission receipt: operation id, phase, and how to poll.

        Raises:
            ScanSubmissionError: If the operation row could not be persisted.
        """
        try:
            operation_id = str(uuid.uuid4())
            self._operation_service.create(
                batch_type=BatchType.JMETER_TEST,
                operation_id=operation_id,
                metadata={
                    "name": "JMeter load test",
                    "target_url": request.target_url,
                    "method": request.method.value,
                    "request": request.model_dump(mode="json", by_alias=True),
                },
            )

            self._logger.info(
                "Queued JMeter load test with operation_id: %s", operation_id
            )
            return JMeterTestSubmissionResponse(
                operation_id=operation_id,
                status=OperationPhase.QUEUED,
                message=(
                    f"JMeter load test queued with operation_id: {operation_id}. "
                    "It will start as soon as a JMeter worker is available. Check "
                    "status at API api/history/get-result with param operation_id: "
                    f"{operation_id}"
                ),
            )
        except Exception as e:
            raise ScanSubmissionError(
                f"Error submitting JMeter load test:\n{str(e)}"
            ) from e

    @staticmethod
    def request_from_metadata(metadata: dict) -> JMeterTestRequest:
        """Rebuild the typed request from a persisted queue row.

        Used by the Runtime to resume a ``QUEUED`` operation with nothing held
        in memory. Raises on malformed metadata; the caller treats that as an
        unrecoverable operation.
        """
        return JMeterTestRequest.model_validate(metadata["request"])

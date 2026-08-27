"""OWASP ZAP evaluation flow.

Orchestrates a single OWASP ZAP scan around the **existing** Operation lifecycle:

    create (PENDING) -> start (RUNNING) -> run scan -> complete (COMPLETED)
                                                    \\-> fail (FAILED) on error

Each transition goes through ``OperationService``, which publishes a domain event
via the existing ``EventPublisher`` — so the existing notification pipeline reacts
without any new wiring.

The scan itself is a synchronous, injected callable (``scan_runner``): in
production it runs over the existing ``ZapClient`` (see
``app.services.tools.owasp_zap.sync_runner``);
tests inject a fake. There is no worker, queue, background thread, new
repository, or new event system — persistence and events flow through the
existing foundation only.
"""

from typing import TYPE_CHECKING, Any, Callable

from app.constants.batch import BatchType

if TYPE_CHECKING:  # runtime-light: OperationService/request are injected, not imported
    from app.schemas.tools.owasp_zap.api_scan import ZapApiScanRequest
    from app.services.operations.operation_service import OperationService

# A scan runner performs one scan synchronously and returns its result (or raises).
ScanRunner = Callable[[Any], Any]


class EvaluationService:
    """Runs an OWASP ZAP evaluation through the existing Operation lifecycle."""

    def __init__(
        self,
        operation_service: "OperationService",
        scan_runner: ScanRunner,
    ) -> None:
        """
        Args:
            operation_service: Existing lifecycle service (owns persistence +
                event publication).
            scan_runner: Callable that runs the scan synchronously and returns a
                result, or raises on failure.
        """
        self._operations = operation_service
        self._run_scan = scan_runner

    def run_api_scan(self, request: "ZapApiScanRequest") -> str:
        """Create and run an API-scan operation synchronously.

        Lifecycle: ``create`` -> ``start`` -> run scan -> ``complete`` with the
        result, or on error ``fail`` with the message and re-raise. Every step
        publishes a domain event through ``OperationService``.

        Returns:
            The operation id.

        Raises:
            Exception: Whatever the scan runner raised (after the operation has
                been marked FAILED and the ``operation.failed`` event published).
        """
        operation_id = self._operations.create(
            BatchType.API_SCAN,
            metadata={
                "target_url": request.url,
                "method": getattr(request.method, "value", request.method),
                "report_group": getattr(request, "report_group", None),
            },
        )
        self._operations.start(operation_id)

        try:
            result = self._run_scan(request)
        except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
            self._operations.fail(operation_id, str(exc))
            raise

        self._operations.complete(operation_id, result)
        return operation_id

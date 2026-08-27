"""Operation application service.

Owns the Operation lifecycle: create an operation, transition its status, and
publish a domain event on every transition via the EventPublisher port.

Design notes:
- Persistence goes through the existing ``BaseOperationDAO`` abstraction — no new
  repository port is introduced and the DAO's provider selection is unchanged.
- Event delivery is decoupled behind the ``EventPublisher`` port; at MVP the
  in-process dispatcher implements it.
- This service is deliberately unaware of OWASP ZAP, notifications, and HTTP.
  Those integrations are wired in later slices. It keeps the existing domain
  vocabulary (Operation, batch_type, OperationPhase).
"""

from typing import TYPE_CHECKING, Any, Callable, Optional
from uuid import uuid4

from app.core.events.contracts import (
    OperationCancelled,
    OperationCompleted,
    OperationCreated,
    OperationFailed,
    OperationProgressed,
    OperationStarted,
)
from app.core.ports.event_publisher import EventPublisher
from app.domain.findings import FindingSummary
from app.domain.lifecycle import OperationPhase

if TYPE_CHECKING:  # keep runtime imports light (no psycopg2/boto3 pulled in here)
    from app.dao.base import BaseOperationDAO
    from app.dao.operation_record import OperationRecord

# Event names remain re-exported for routing tables that key on the wire name.
# They are read off the typed contracts, so the literals exist in exactly one
# place (app.core.events.contracts) and can never drift from what is published.
EVENT_OPERATION_CREATED = OperationCreated.name
EVENT_OPERATION_STARTED = OperationStarted.name
EVENT_OPERATION_COMPLETED = OperationCompleted.name
EVENT_OPERATION_FAILED = OperationFailed.name
EVENT_OPERATION_CANCELLED = OperationCancelled.name
# Optional, intermediate progress between STARTED and a terminal event. Emitting
# it is opt-in: a tool that can report progress calls report_progress(); one
# that cannot simply never does, and only the guaranteed lifecycle events flow.
EVENT_OPERATION_PROGRESS = OperationProgressed.name


def _default_id_suffix() -> str:
    """Unique suffix used to build an operation id (overridable in tests)."""
    return uuid4().hex


class OperationService:
    """Creates operations and drives their lifecycle, publishing an event per step."""

    def __init__(
        self,
        dao: "BaseOperationDAO",
        publisher: EventPublisher,
        id_suffix: Callable[[], str] = _default_id_suffix,
    ) -> None:
        """
        Args:
            dao: The persistence abstraction (existing ``BaseOperationDAO``).
            publisher: The event-publishing port; the in-process dispatcher at MVP.
            id_suffix: Factory for the unique part of an operation id. Injectable
                so tests can produce deterministic ids.
        """
        self._dao = dao
        self._publisher = publisher
        self._id_suffix = id_suffix

    def create(
        self,
        batch_type: str,
        metadata: Optional[dict[str, Any]] = None,
        log_path: Optional[str] = None,
        operation_id: Optional[str] = None,
    ) -> str:
        """Create a new QUEUED operation, persist it, and publish ``operation.created``.

        The operation is persisted with ``status=QUEUED`` — this *is* the work
        queue entry. A Pool Manager (or equivalent dispatcher) later fetches
        queued operations ordered by creation time and assigns them to a
        registered worker; nothing is held in an in-memory queue.

        Args:
            batch_type: Category / tool type for this operation (see ``BatchType``).
            metadata: Arbitrary creation-time context (targets, config, ...).
            log_path: Optional path to the operation's log file.
            operation_id: Optional caller-supplied id. When given it is used
                verbatim (a tool that already owns an id scheme keeps it);
                when ``None`` the ``"<batch_type>:<suffix>"`` form is generated.

        Returns:
            The newly created operation id (caller-supplied, or
            ``"<batch_type>:<suffix>"``).
        """
        operation_id = operation_id or f"{batch_type}:{self._id_suffix()}"
        self._dao.create_operation(
            op_id=operation_id,
            status=OperationPhase.QUEUED,
            metadata=metadata or {},
            batch_type=batch_type,
            log_path=log_path,
        )
        self._publisher.publish(
            OperationCreated(operation_id=operation_id, batch_type=batch_type)
        )
        return operation_id

    def start(self, operation_id: str) -> None:
        """Transition an operation to RUNNING and publish ``operation.started``."""
        self._dao.update_operation_status(
            operation_id=operation_id, status=OperationPhase.RUNNING
        )
        self._publisher.publish(OperationStarted(operation_id=operation_id))

    def complete(
        self,
        operation_id: str,
        result: Any = None,
        *,
        findings: Optional[dict[str, Any]] = None,
    ) -> None:
        """Transition an operation to COMPLETED and publish ``operation.completed``.

        Args:
            operation_id: The operation to complete.
            result: Full result payload persisted to storage (unchanged).
            findings: Optional generic severity breakdown (e.g.
                ``{"critical": 0, "high": 1, ...}``) forwarded in the published
                event so the notification layer can render a summary without
                loading or parsing the persisted result. Tool-agnostic: any tool
                that has a severity summary passes one; those that don't omit it.
        """
        self._dao.update_operation_status(
            operation_id=operation_id,
            status=OperationPhase.COMPLETED,
            result=result,
        )
        self._publisher.publish(
            OperationCompleted(
                operation_id=operation_id,
                findings=(
                    FindingSummary.from_mapping(findings)
                    if findings is not None
                    else None
                ),
            )
        )

    def fail(self, operation_id: str, error: str) -> None:
        """Transition an operation to FAILED and publish ``operation.failed``."""
        self._dao.update_operation_status(
            operation_id=operation_id,
            status=OperationPhase.FAILED,
            error=error,
        )
        self._publisher.publish(
            OperationFailed(operation_id=operation_id, error=error)
        )

    def cancel(self, operation_id: str) -> None:
        """Transition an operation to CANCELLED and publish ``operation.cancelled``."""
        self._dao.update_operation_status(
            operation_id=operation_id, status=OperationPhase.CANCELLED
        )
        self._publisher.publish(OperationCancelled(operation_id=operation_id))

    def report_progress(
        self,
        operation_id: str,
        *,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        """Publish ``operation.progress`` for an in-flight operation.

        Purely additive to the lifecycle: it does not persist a status change and
        does not alter any transition — it only emits an intermediate progress
        event a tool can raise while RUNNING. A tool that cannot report progress
        never calls this, so the notification layer simply sees no progress
        events (and platforms without progress support ignore them). The payload
        stays generic (no tool vocabulary); the notification layer maps it into
        its own progress model.

        Args:
            operation_id: The operation this progress refers to.
            stage: Optional human-readable stage label.
            progress: Optional completion percentage in ``[0, 100]``.
            message: Optional free-text detail.
            worker_id: Optional id of the worker executing the operation.
        """
        self._publisher.publish(
            OperationProgressed(
                operation_id=operation_id,
                stage=stage,
                progress=progress,
                message=message,
                worker_id=worker_id,
            )
        )

    def get(self, operation_id: str) -> "Optional[OperationRecord]":
        """Return an operation by id, delegating to the DAO (or ``None``)."""
        return self._dao.get_operation(operation_id)

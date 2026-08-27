"""Operations context — public API.

Exposes the :class:`OperationService` (the single owner of operation lifecycle
transitions, persistence, and event publication) and the ``EVENT_OPERATION_*``
constants. The event names are public on contract grounds: they are the
published vocabulary that crosses context boundaries — subscribers match on
these names.
"""

from app.services.operations.operation_service import (
    EVENT_OPERATION_CANCELLED,
    EVENT_OPERATION_COMPLETED,
    EVENT_OPERATION_CREATED,
    EVENT_OPERATION_FAILED,
    EVENT_OPERATION_PROGRESS,
    EVENT_OPERATION_STARTED,
    OperationService,
)

__all__ = [
    "EVENT_OPERATION_CANCELLED",
    "EVENT_OPERATION_COMPLETED",
    "EVENT_OPERATION_CREATED",
    "EVENT_OPERATION_FAILED",
    "EVENT_OPERATION_PROGRESS",
    "EVENT_OPERATION_STARTED",
    "OperationService",
]

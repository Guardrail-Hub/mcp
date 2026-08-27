"""Registry mapping an operation type to the ZapOperation that runs it.

Process-wide, populated once at startup from the composition root
(``app.bootstrap.build_zap_operation_registry``). The Dispatcher holds a
reference to this registry and nothing else tool-specific: it resolves the
:class:`ZapOperation` for a queued row, asks it to deserialize the request, and
routes on its execution strategy.

Backward compatibility: a queue row persisted before this refactor has no
``operation_type`` in its metadata. :meth:`resolve_from_metadata` therefore
defaults a missing type to :data:`ZapOperationType.API_SCAN`, so no existing
queued operation becomes invalid after the migration.
"""

from typing import Dict

from app.services.tools.owasp_zap.execution.handler import ZapOperation
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType

# Legacy rows (pre-refactor) carry no operation_type — treat them as API_SCAN,
# the only tool that existed when they were queued.
_DEFAULT_OPERATION_TYPE = ZapOperationType.API_SCAN


class UnknownOperationTypeError(KeyError):
    """Raised when no ZapOperation is registered for a resolved operation type.

    The Dispatcher treats this like unusable metadata: the operation can never
    run, so it is marked FAILED (and any assigned worker released) rather than
    stranding a worker forever.
    """


class ZapOperationRegistry:
    """Resolves the :class:`ZapOperation` for an operation type or queue row."""

    def __init__(self) -> None:
        self._operations: Dict[ZapOperationType, ZapOperation] = {}

    def register(self, operation: ZapOperation) -> None:
        """Register one operation. Rejects a duplicate type (fail fast at boot)."""
        if operation.operation_type in self._operations:
            raise ValueError(
                f"An operation is already registered for {operation.operation_type!r}"
            )
        self._operations[operation.operation_type] = operation

    def get(self, operation_type: ZapOperationType) -> ZapOperation:
        """Return the registered operation for *operation_type*.

        Raises:
            UnknownOperationTypeError: if nothing is registered for it.
        """
        try:
            return self._operations[operation_type]
        except KeyError as exc:
            raise UnknownOperationTypeError(
                f"No handler registered for operation type {operation_type!r}"
            ) from exc

    def resolve_from_metadata(self, metadata: dict) -> ZapOperation:
        """Resolve the operation for a persisted queue row.

        Reads ``metadata["operation_type"]``, defaulting to ``API_SCAN`` when it
        is absent (legacy rows), coerces it to the enum, and looks it up.

        Raises:
            UnknownOperationTypeError: unknown/unregistered type.
            ValueError: the stored value is not a valid ``ZapOperationType``.
        """
        raw = (metadata or {}).get("operation_type")
        operation_type = (
            _DEFAULT_OPERATION_TYPE if raw is None else ZapOperationType(raw)
        )
        return self.get(operation_type)

    def registered_types(self) -> frozenset[ZapOperationType]:
        """Return the set of currently registered operation types (diagnostics)."""
        return frozenset(self._operations)

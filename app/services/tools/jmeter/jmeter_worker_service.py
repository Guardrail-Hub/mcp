"""JMeter worker lifecycle service.

Owns the application-layer orchestration of the JMeter worker pool that spans
more than one lower layer — specifically the parts of worker management that
touch both the Worker Registry (an integration) and the Operations store (the
DAO). The HTTP worker router is a thin adapter over this service and never
talks to the registry or the DAO directly, honouring the
``routers -> services -> integrations|dao`` dependency rule
(``.ai/standards/architecture/backend-layering`` R4).

Registration, heartbeat, listing and draining delegate straight to the Registry,
which owns worker state. ``remove`` additionally fails any operation the removed
worker was running — the one worker action with a cross-layer side effect — via
:meth:`fail_orphaned_operation`, the single home for "mark an operation FAILED
because its worker went away", shared with the dispatcher's heartbeat sweep.
"""

from typing import List, Optional

from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.operation_dao import get_operation_dao
from app.domain.lifecycle import OperationPhase
from app.integrations.jmeter.registry import JMeterWorkerRegistry
from app.integrations.jmeter.runtime import registry as runtime_registry
from app.schemas.tools.jmeter.worker import JMeterWorkerInfo


class JMeterWorkerService:
    """Application service for JMeter worker registration, heartbeat and removal."""

    def __init__(self, registry: Optional[JMeterWorkerRegistry] = None) -> None:
        """
        Args:
            registry: The pool registry; defaults to the process-wide singleton
                so the router and the dispatcher share one pool.
        """
        self._registry = registry or runtime_registry
        self._logger = MCPLogger("JMeterWorkerService")

    # ── Registry-backed operations ───────────────────────────────────────

    def register(
        self,
        *,
        worker_id: Optional[str],
        hostname: str,
        endpoint: str,
        port: int,
    ) -> JMeterWorkerInfo:
        """Register (or re-register) a worker. Delegates to the Registry."""
        return self._registry.register(
            worker_id=worker_id,
            hostname=hostname,
            endpoint=endpoint,
            port=port,
        )

    def heartbeat(self, worker_id: str) -> JMeterWorkerInfo:
        """Record a heartbeat. Raises ``JMeterWorkerNotRegisteredError`` if unknown."""
        return self._registry.heartbeat(worker_id)

    def list_workers(self) -> List[JMeterWorkerInfo]:
        """Return a snapshot of every registered worker."""
        return self._registry.list_all()

    def drain(self, worker_id: str) -> JMeterWorkerInfo:
        """Begin graceful maintenance (Active -> Draining)."""
        return self._registry.mark_draining(worker_id)

    def remove(self, worker_id: str) -> Optional[str]:
        """Hard-remove *worker_id*; fail its in-flight operation if any.

        Returns the orphaned ``operation_id``, or ``None``. Failing that
        operation is required: a worker removed from the registry can never be
        assigned to again, so leaving the operation RUNNING would strand it.
        """
        orphaned_op_id = self._registry.remove(worker_id)
        if orphaned_op_id is not None:
            self.fail_orphaned_operation(
                operation_id=orphaned_op_id,
                worker_id=worker_id,
                reason=(
                    f"JMeter worker '{worker_id}' was removed from the registry "
                    "while running this operation."
                ),
            )
        return orphaned_op_id

    # ── Cross-layer side effect (shared with the dispatcher) ──────────────

    def fail_orphaned_operation(
        self,
        *,
        operation_id: str,
        worker_id: str,
        reason: str,
        operation_dao: Optional[BaseOperationDAO] = None,
    ) -> None:
        """Mark *operation_id* FAILED because its worker went away.

        Single home for the "worker died or was removed -> fail its operation"
        rule, used by both the worker router (via :meth:`remove`) and the
        dispatcher's heartbeat sweep. *operation_dao* lets a caller that already
        holds a DAO reuse it; otherwise the configured one is resolved lazily.

        A ``RuntimeError`` from DAO resolution means ``DATABASE_PROVIDER=NONE``
        (no persistence configured) — there is nothing to update, so it is
        swallowed intentionally.
        """
        try:
            dao = operation_dao or get_operation_dao()
            dao.update_operation_status(
                operation_id=operation_id,
                status=OperationPhase.FAILED,
                error=reason,
            )
        except RuntimeError:
            self._logger.debug(
                "No persistence configured; skipped failing orphaned operation '%s'",
                operation_id,
            )

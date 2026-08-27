"""ZAP worker lifecycle service.

Owns the *application-layer* orchestration of the ZAP worker pool that spans
more than one lower layer — specifically the parts of worker management that
touch both the Worker Registry (an integration) and the Operations store (the
DAO). The HTTP worker router (``routers/public/zap_worker_router.py``) is a
thin adapter over this service and never talks to the registry or the DAO
directly, honouring the ``routers -> services -> integrations|dao`` dependency
rule (see ``.ai/standards/architecture/backend-layering``).

Registration, heartbeat, listing and draining are delegated straight to the
Registry (the domain owner of worker state). ``remove`` additionally fails any
operation the removed worker was running — the one worker action that has a
cross-layer side effect — via :meth:`fail_orphaned_operation`, which is the
single home for "mark an operation FAILED because its worker went away" so the
router and the background dispatcher share one implementation.
"""

from typing import List, Optional

from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.operation_dao import get_operation_dao
from app.integrations.owasp_zap.registry import OwaspZapWorkerRegistry
from app.integrations.owasp_zap.runtime import registry as runtime_registry
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo


class ZapWorkerService:
    """Application service for ZAP worker registration, heartbeat and removal."""

    def __init__(self, registry: Optional[OwaspZapWorkerRegistry] = None) -> None:
        self._registry = registry or runtime_registry
        self._logger = MCPLogger("ZapWorkerService")

    # ── Registry-backed operations ───────────────────────────────────────

    def register(
        self,
        *,
        worker_id: Optional[str],
        hostname: str,
        endpoint: str,
        port: int,
        version: Optional[str] = None,
    ) -> ZapWorkerInfo:
        """Register (or re-register) a worker. Delegates to the Registry."""
        return self._registry.register(
            worker_id=worker_id,
            hostname=hostname,
            endpoint=endpoint,
            port=port,
            version=version,
        )

    def heartbeat(self, worker_id: str) -> ZapWorkerInfo:
        """Record a heartbeat. Raises ``WorkerNotRegisteredError`` if unknown."""
        return self._registry.heartbeat(worker_id)

    def list_workers(self) -> List[ZapWorkerInfo]:
        """Return a snapshot of every registered worker."""
        return self._registry.list_all()

    def drain(self, worker_id: str) -> ZapWorkerInfo:
        """Begin graceful maintenance (Active -> Draining)."""
        return self._registry.mark_draining(worker_id)

    def remove(self, worker_id: str) -> Optional[str]:
        """Hard-remove *worker_id*; fail its in-flight operation if any.

        Returns the orphaned ``operation_id`` (or ``None``). Marking that
        operation FAILED is required because a worker removed from the
        registry can never be assigned to again — leaving the operation
        RUNNING would strand it forever.
        """
        orphaned_op_id = self._registry.remove(worker_id)
        if orphaned_op_id is not None:
            self.fail_orphaned_operation(
                operation_id=orphaned_op_id,
                worker_id=worker_id,
                reason=(
                    f"Worker '{worker_id}' was removed from the registry while "
                    "running this operation."
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

        Single home for the "worker died/removed -> fail its operation" rule,
        used by both the worker router (via :meth:`remove`) and the background
        dispatcher's heartbeat sweep. *operation_dao* lets a caller that
        already holds a DAO (the dispatcher) reuse it; otherwise the
        provider-configured DAO is resolved lazily.

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

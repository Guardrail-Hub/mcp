"""
ZAP queue dispatcher.

Two independent background loops, both started/stopped together:

1. **Queue loop** — polls the Operations table for ``QUEUED`` rows (oldest
   first) and dispatches each **generically**: it resolves the row's
   :class:`ZapOperation` from the :class:`ZapOperationRegistry` (by the
   strongly-typed ``operation_type`` in metadata) and routes on the operation's
   :class:`ExecutionStrategy`. Worker-bound operations are assigned to an idle,
   Active worker via the Pool Manager and executed on it; orchestration
   operations (fan-out/fan-in) run on a small orchestration executor and never
   occupy a worker. The Dispatcher knows nothing about which concrete tool it is
   running — that indirection is the whole point of the registry. An operation
   that can't be assigned this tick simply stays ``QUEUED`` and is retried next
   tick; there is no in-memory queue to lose on restart, because the row itself
   *is* the queue entry.

2. **Heartbeat sweep loop** — periodically asks the Worker Registry to mark
   any worker whose heartbeat has expired as Offline. If that worker was
   executing an operation, this loop marks that operation FAILED. There is no
   automatic retry — resubmitting is left to the user.

Both loops run on plain daemon threads (matching the rest of this codebase's
synchronous, thread-pool style — see ``ThreadPoolExecutor`` in
``api_scanner.py``) rather than asyncio tasks, so they work the same whether
the ASGI server uses one worker process or several.
"""

import threading
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Optional

from app.constants.batch import BatchType
from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.integrations.owasp_zap.pool import OwaspZapPoolManager
from app.dao.operation_record import OperationRecord
from app.schemas.tools.owasp_zap.worker import ZapWorkerLifecycleState
from app.services.operations.operation_service import OperationService
from app.services.tools.owasp_zap.execution.handler import ZapOperation
from app.services.tools.owasp_zap.execution.registry import ZapOperationRegistry


class OwaspZapDispatcher:
    """Background dispatcher tying the Operations queue to the Pool Manager."""

    # How often the periodic dispatcher health summary is emitted at INFO.
    # A liveness heartbeat for operators without the per-tick polling noise.
    _HEALTH_SUMMARY_INTERVAL_SECONDS: float = 300.0

    def __init__(
        self,
        *,
        operation_dao: BaseOperationDAO,
        operation_service: OperationService,
        pool_manager: OwaspZapPoolManager,
        registry: ZapOperationRegistry,
        queue_poll_interval_seconds: float,
        heartbeat_sweep_interval_seconds: float,
        heartbeat_timeout_seconds: float,
        orchestration_executor: Optional[Executor] = None,
    ) -> None:
        self._operation_dao = operation_dao
        # FAILED transitions go through OperationService (state + persistence +
        # event). The DAO is retained only for queue *reads* (get_queued_operations),
        # which OperationService does not own. Scheduling/sweep logic is unchanged.
        self._operation_service = operation_service
        self._pool_manager = pool_manager
        # The generic registry replaces the former direct ZapApiScanService
        # dependency: the Dispatcher resolves each row's ZapOperation here and
        # never names a concrete tool or request model.
        self._registry = registry
        # Orchestration strategies (fan-out/fan-in) run here, off the queue,
        # WITHOUT claiming a ZAP worker. Small by design — orchestrators only do
        # DB polling + report aggregation, never scanning. Bounded so a burst of
        # orchestration operations cannot spawn unbounded threads.
        self._orchestration_executor = orchestration_executor or ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="zap-orchestration"
        )
        self._queue_poll_interval = queue_poll_interval_seconds
        self._sweep_interval = heartbeat_sweep_interval_seconds
        self._heartbeat_timeout = heartbeat_timeout_seconds

        self._logger = MCPLogger("OwaspZapDispatcher")
        self._stop_event = threading.Event()
        self._queue_thread: Optional[threading.Thread] = None
        self._sweep_thread: Optional[threading.Thread] = None

        # Observability state (logging only — no effect on scheduling).
        # Last observed queue depth, so INFO is emitted on change rather than
        # on every poll; and the timestamp of the last health summary.
        self._last_queue_length: Optional[int] = None
        self._last_summary_monotonic: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start both background loops. Idempotent — a second call is a no-op."""
        if self._queue_thread is not None:
            return

        self._stop_event.clear()
        self._queue_thread = threading.Thread(
            target=self._run_queue_loop, name="zap-queue-dispatcher", daemon=True
        )
        self._sweep_thread = threading.Thread(
            target=self._run_sweep_loop, name="zap-heartbeat-sweeper", daemon=True
        )
        self._queue_thread.start()
        self._sweep_thread.start()
        self._logger.info(
            "Dispatcher started (queue_poll=%ss, heartbeat_sweep=%ss, heartbeat_timeout=%ss)",
            self._queue_poll_interval,
            self._sweep_interval,
            self._heartbeat_timeout,
        )

    def stop(self, timeout_seconds: float = 5.0) -> None:
        """Signal both loops to stop and wait (briefly) for them to exit."""
        self._stop_event.set()
        for thread in (self._queue_thread, self._sweep_thread):
            if thread is not None:
                thread.join(timeout=timeout_seconds)
        self._queue_thread = None
        self._sweep_thread = None
        # Stop accepting new orchestration work; in-flight orchestrators (if any)
        # are left to finish their own cleanup. Never blocks shutdown.
        self._orchestration_executor.shutdown(wait=False)
        self._logger.info("Dispatcher stopped")

    # ── Queue loop ───────────────────────────────────────────────────────

    def _run_queue_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._dispatch_queued_operations()
            except Exception as exc:  # pylint: disable=broad-except
                # A polling-loop failure must never kill the loop itself.
                self._logger.error("Queue dispatch tick failed: %s", exc)
            self._stop_event.wait(self._queue_poll_interval)

    def _dispatch_queued_operations(self) -> None:
        # Poll only the ZAP lane. Without this filter the loop below would pick up
        # another engine's rows, fail to resolve them against the ZAP registry, and
        # mark them FAILED — see ADR-0011.
        queued = self._operation_dao.get_queued_operations(BatchType.ALL_SCAN_TYPES)

        # Every poll is DEBUG-only so it never floods production logs.
        self._logger.debug("Polling queued operations (found %d)", len(queued))
        # Meaningful signal (queue depth change) is promoted to INFO here.
        self._log_queue_depth_change(len(queued))
        # Periodic liveness summary, independent of whether work was found.
        self._maybe_log_health_summary()

        if not queued:
            return

        for operation in queued:
            self._dispatch_one(operation)

    def _dispatch_one(self, operation: OperationRecord) -> None:
        """Resolve *operation*'s handler and route it by execution strategy.

        Fully generic: the Dispatcher resolves the :class:`ZapOperation` from the
        registry (never a concrete tool) and branches only on the operation's
        ``execution_strategy.requires_worker`` — a tool-agnostic capability.
        """
        try:
            zap_operation = self._registry.resolve_from_metadata(operation.metadata)
        except Exception as exc:  # pylint: disable=broad-except
            # Unknown / invalid / unregistered operation type — it can never run.
            # No worker has been claimed yet, so just fail it loudly.
            self._logger.error(
                "Operation '%s' has an unresolvable operation type, marking FAILED: %s",
                operation.operation_id,
                exc,
            )
            self._operation_service.fail(
                operation.operation_id,
                f"Could not resolve a handler for this operation: {exc}",
            )
            return

        if zap_operation.execution_strategy.requires_worker:
            self._dispatch_worker_bound(operation, zap_operation)
        else:
            self._dispatch_orchestration(operation, zap_operation)

    def _dispatch_worker_bound(
        self, operation: OperationRecord, zap_operation: ZapOperation
    ) -> None:
        """Assign a ZAP worker and execute a worker-bound operation on it."""
        worker = self._pool_manager.try_assign(operation.operation_id)
        if worker is None:
            # No idle Active worker right now — stays QUEUED, retried next tick.
            return

        try:
            request = zap_operation.deserialize_request(operation.metadata)
        except Exception as exc:  # pylint: disable=broad-except
            # Malformed/legacy metadata can never be resumed — fail loudly
            # rather than silently holding a worker slot forever.
            self._logger.error(
                "Operation '%s' has unusable metadata, marking FAILED: %s",
                operation.operation_id,
                exc,
            )
            self._pool_manager.release_worker(worker.worker_id, operation.operation_id)
            self._operation_service.fail(
                operation.operation_id,
                f"Could not reconstruct scan request from metadata: {exc}",
            )
            return

        zap_operation.handler.execute(operation.operation_id, request, worker)

    def _dispatch_orchestration(
        self, operation: OperationRecord, zap_operation: ZapOperation
    ) -> None:
        """Run an orchestration operation off the queue, without a ZAP worker.

        The operation is transitioned to RUNNING *synchronously before* it is
        handed to the orchestration executor: that removes it from the QUEUED set
        immediately, so the next poll tick cannot dispatch the same orchestrator
        twice. The handler then only spawns/aggregates child operations (its
        ``worker`` argument is ``None``) — no worker is ever claimed.
        """
        try:
            request = zap_operation.deserialize_request(operation.metadata)
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error(
                "Orchestration operation '%s' has unusable metadata, marking FAILED: %s",
                operation.operation_id,
                exc,
            )
            self._operation_service.fail(
                operation.operation_id,
                f"Could not reconstruct request from metadata: {exc}",
            )
            return

        self._operation_service.start(operation.operation_id)
        self._orchestration_executor.submit(
            zap_operation.handler.execute, operation.operation_id, request, None
        )

    # ── Observability helpers (logging only) ─────────────────────────────

    def _log_queue_depth_change(self, current_length: int) -> None:
        """Emit INFO only when the queue depth actually changes.

        The first observation announces an existing backlog (if any); after
        that, transitions are logged as ``X -> Y``. Steady-state polling never
        produces a line here.
        """
        previous = self._last_queue_length
        if previous is None:
            if current_length > 0:
                self._logger.info("Found %d queued operation(s)", current_length)
        elif current_length != previous:
            self._logger.info(
                "Queue length changed: %d -> %d", previous, current_length
            )
        self._last_queue_length = current_length

    def _maybe_log_health_summary(self) -> None:
        """Emit a concise dispatcher health summary at most once per interval."""
        now = time.monotonic()
        if now - self._last_summary_monotonic < self._HEALTH_SUMMARY_INTERVAL_SECONDS:
            return
        self._last_summary_monotonic = now
        self._logger.info(self._build_health_summary())

    def _build_health_summary(self) -> str:
        """Build a one-line snapshot of worker pool and queue state."""
        workers = self._pool_manager.registry.list_all()
        active = [w for w in workers if w.state == ZapWorkerLifecycleState.ACTIVE]
        busy = [w for w in active if w.op_id is not None]
        idle = [w for w in active if w.op_id is None]
        running = [w for w in workers if w.op_id is not None]
        queue_length = self._last_queue_length or 0
        return (
            "Dispatcher health — "
            f"active={len(active)}, idle={len(idle)}, busy={len(busy)}, "
            f"queue={queue_length}, running={len(running)}"
        )

    # ── Heartbeat sweep loop ─────────────────────────────────────────────

    def _run_sweep_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sweep_expired_workers()
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.error("Heartbeat sweep tick failed: %s", exc)
            self._stop_event.wait(self._sweep_interval)

    def _sweep_expired_workers(self) -> None:
        orphaned = self._pool_manager.registry.sweep_expired(self._heartbeat_timeout)
        for worker_id, operation_id in orphaned:
            self._logger.warning(
                "Worker '%s' went Offline while executing '%s' — marking FAILED (no auto-retry)",
                worker_id,
                operation_id,
            )
            self._operation_service.fail(
                operation_id,
                (
                    f"Worker '{worker_id}' stopped sending heartbeats and was marked Offline "
                    "while this operation was running. Resubmit if you want to retry."
                ),
            )

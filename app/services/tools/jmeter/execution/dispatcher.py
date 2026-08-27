"""JMeter queue dispatcher.

Two independent background loops, started and stopped together — the same shape
as ``OwaspZapDispatcher``, with JMeter's own registry, pool and lane:

1. **Queue loop** — polls the Operations table for ``QUEUED`` rows **in the
   JMeter lane only**, oldest first, and hands each to the Runtime once a
   worker has been assigned. An operation that cannot be assigned this tick
   stays ``QUEUED`` and is retried next tick; there is no in-memory queue to
   lose on restart, because the row itself is the queue entry.

2. **Heartbeat sweep loop** — periodically asks the Worker Registry to mark any
   worker whose heartbeat has expired as Offline. If that worker was executing
   an operation, this loop marks that operation FAILED. There is no automatic
   retry — resubmitting is left to the user.

**Lane isolation.** ``get_queued_operations`` requires the caller's lane, and
this dispatcher passes :attr:`LANE` and nothing else. A ZAP row is therefore
never visible here, and a JMeter row is never visible to the ZAP dispatcher
(ADR-0011). The lane is a class attribute rather than a constructor argument
because it is a property of *what this dispatcher is*, not something a
deployment should be able to widen.

Unlike the ZAP dispatcher there is no handler registry: JMeter has exactly one
operation type, so there is nothing to route (ADR-0009 — do not abstract over a
set of one). Every row in this lane is a load test, and the Runtime runs it.

Both loops run on plain daemon threads, matching the rest of this codebase's
synchronous, thread-pool style, so they behave the same whether the ASGI server
runs one worker process or several.
"""

import threading
import time
from typing import Optional

from app.constants.batch import BatchType
from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.operation_record import OperationRecord
from app.integrations.jmeter.pool import JMeterWorkerPoolManager
from app.schemas.tools.jmeter.worker import JMeterWorkerState
from app.services.operations.operation_service import OperationService
from app.services.tools.jmeter.execution.runtime import JMeterRuntime
from app.services.tools.jmeter.run_jmeter_test_service import JMeterTestService


class JMeterDispatcher:
    """Background dispatcher tying the JMeter queue lane to the worker pool."""

    #: The queue lane this dispatcher consumes. Never widened.
    LANE = BatchType.ALL_JMETER_TYPES

    # How often the periodic dispatcher health summary is emitted at INFO.
    # A liveness heartbeat for operators without the per-tick polling noise.
    _HEALTH_SUMMARY_INTERVAL_SECONDS: float = 300.0

    def __init__(
        self,
        *,
        operation_dao: BaseOperationDAO,
        operation_service: OperationService,
        pool_manager: JMeterWorkerPoolManager,
        runtime: JMeterRuntime,
        queue_poll_interval_seconds: float,
        heartbeat_sweep_interval_seconds: float,
        heartbeat_timeout_seconds: float,
        logger: Optional[MCPLogger] = None,
    ) -> None:
        """
        Args:
            operation_dao: Queue *reads* only — ``get_queued_operations(LANE)``.
                Lifecycle transitions go through ``operation_service``.
            operation_service: The transitions this dispatcher owns: failing an
                operation it could not hand off, and failing one orphaned by a
                dead worker.
            pool_manager: Assigns and releases workers.
            runtime: Executes one operation once a worker is assigned.
            queue_poll_interval_seconds: How often the lane is polled.
            heartbeat_sweep_interval_seconds: How often worker liveness is swept.
            heartbeat_timeout_seconds: Silence after which a worker is Offline.
                Configured independently of ZAP's — a worker saturated by load
                generation heartbeats less promptly than an idle daemon.
            logger: Optional logger; one is created if omitted.
        """
        self._operation_dao = operation_dao
        self._operation_service = operation_service
        self._pool_manager = pool_manager
        self._runtime = runtime
        self._queue_poll_interval = queue_poll_interval_seconds
        self._sweep_interval = heartbeat_sweep_interval_seconds
        self._heartbeat_timeout = heartbeat_timeout_seconds

        self._logger = logger or MCPLogger("JMeterDispatcher")
        self._stop_event = threading.Event()
        self._queue_thread: Optional[threading.Thread] = None
        self._sweep_thread: Optional[threading.Thread] = None

        # Observability state (logging only — no effect on scheduling).
        self._last_queue_length: Optional[int] = None
        self._last_summary_monotonic: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start both background loops. Idempotent — a second call is a no-op."""
        if self._queue_thread is not None:
            return

        self._stop_event.clear()
        self._queue_thread = threading.Thread(
            target=self._run_queue_loop, name="jmeter-queue-dispatcher", daemon=True
        )
        self._sweep_thread = threading.Thread(
            target=self._run_sweep_loop, name="jmeter-heartbeat-sweeper", daemon=True
        )
        self._queue_thread.start()
        self._sweep_thread.start()
        self._logger.info(
            "JMeter dispatcher started (queue_poll=%ss, heartbeat_sweep=%ss, "
            "heartbeat_timeout=%ss)",
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
        self._logger.info("JMeter dispatcher stopped")

    # ── Queue loop ───────────────────────────────────────────────────────

    def _run_queue_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._dispatch_queued_operations()
            except Exception as exc:  # pylint: disable=broad-except
                # A polling-loop failure must never kill the loop itself.
                self._logger.error("JMeter queue dispatch tick failed: %s", exc)
            self._stop_event.wait(self._queue_poll_interval)

    def _dispatch_queued_operations(self) -> None:
        """One poll tick: read this lane, dispatch whatever can be assigned."""
        queued = self._operation_dao.get_queued_operations(self.LANE)

        # Every poll is DEBUG-only so it never floods production logs.
        self._logger.debug("Polling queued JMeter operations (found %d)", len(queued))
        self._log_queue_depth_change(len(queued))
        self._maybe_log_health_summary()

        for operation in queued:
            self._dispatch_one(operation)

    def _dispatch_one(self, operation: OperationRecord) -> None:
        """Assign a worker to *operation* and hand it to the Runtime.

        Nothing here inspects the operation's type: every row in this lane is a
        JMeter load test. The only decisions are "is a worker free?" and "is the
        persisted request still readable?".
        """
        worker = self._pool_manager.try_assign(operation.operation_id)
        if worker is None:
            # No idle Active worker right now — stays QUEUED, retried next tick.
            return

        try:
            request = JMeterTestService.request_from_metadata(operation.metadata)
        except Exception as exc:  # pylint: disable=broad-except
            # Malformed or legacy metadata can never be resumed — fail loudly
            # rather than silently holding a worker slot forever.
            self._logger.error(
                "JMeter operation '%s' has unusable metadata, marking FAILED: %s",
                operation.operation_id,
                exc,
            )
            self._pool_manager.release_worker(worker.worker_id, operation.operation_id)
            self._operation_service.fail(
                operation.operation_id,
                f"Could not reconstruct the load-test request from metadata: {exc}",
            )
            return

        self._run_on_worker(operation.operation_id, request, worker)

    def _run_on_worker(self, operation_id: str, request, worker) -> None:
        """Hand one assigned operation to the Runtime.

        The Runtime's contract is that it never raises and releases the worker
        on every path. This backstop exists because a contract can be broken:
        if ``run`` does raise, the worker would otherwise stay bound to a dead
        operation forever and the pool would leak a slot per failure. So the
        dispatcher fails the operation and releases the worker itself.

        Until the Runtime is implemented (Phase 3) this is the path every
        assignment takes, and the operation fails with a message saying exactly
        that — which is the honest outcome, and strictly better than leaving the
        row RUNNING or silently QUEUED.
        """
        try:
            self._runtime.run(operation_id, request, worker)
        except NotImplementedError:
            self._logger.warning(
                "JMeter operation '%s' was assigned to worker '%s' but execution "
                "is not implemented yet — marking FAILED",
                operation_id,
                worker.worker_id,
            )
            self._pool_manager.release_worker(worker.worker_id, operation_id)
            self._operation_service.fail(
                operation_id,
                "JMeter execution is not available yet: the worker pool and "
                "dispatch path exist, but the execution runtime has not been "
                "implemented. Resubmit once it is.",
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error(
                "JMeter runtime raised for operation '%s' on worker '%s': %s",
                operation_id,
                worker.worker_id,
                exc,
            )
            self._pool_manager.release_worker(worker.worker_id, operation_id)
            self._operation_service.fail(
                operation_id, f"JMeter execution failed to start: {exc}"
            )

    # ── Observability ────────────────────────────────────────────────────

    def _log_queue_depth_change(self, current_length: int) -> None:
        """Log queue depth only when it actually changes.

        Steady-state polling produces no line here.
        """
        previous = self._last_queue_length
        if previous is None:
            if current_length > 0:
                self._logger.info("Found %d queued JMeter operation(s)", current_length)
        elif current_length != previous:
            self._logger.info(
                "JMeter queue length changed: %d -> %d", previous, current_length
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
        active = [w for w in workers if w.state == JMeterWorkerState.ACTIVE]
        busy = [w for w in active if w.op_id is not None]
        idle = [w for w in active if w.op_id is None]
        running = [w for w in workers if w.op_id is not None]
        queue_length = self._last_queue_length or 0
        return (
            "JMeter dispatcher health — "
            f"active={len(active)}, idle={len(idle)}, busy={len(busy)}, "
            f"queue={queue_length}, running={len(running)}"
        )

    # ── Heartbeat sweep loop ─────────────────────────────────────────────

    def _run_sweep_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sweep_expired_workers()
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.error("JMeter heartbeat sweep tick failed: %s", exc)
            self._stop_event.wait(self._sweep_interval)

    def _sweep_expired_workers(self) -> None:
        """Reap silent workers and fail whatever they were running."""
        orphaned = self._pool_manager.registry.sweep_expired(self._heartbeat_timeout)
        for worker_id, operation_id in orphaned:
            self._logger.warning(
                "JMeter worker '%s' went Offline while executing '%s' — "
                "marking FAILED (no auto-retry)",
                worker_id,
                operation_id,
            )
            self._operation_service.fail(
                operation_id,
                (
                    f"JMeter worker '{worker_id}' stopped sending heartbeats and was "
                    "marked Offline while this operation was running. Resubmit if "
                    "you want to retry."
                ),
            )

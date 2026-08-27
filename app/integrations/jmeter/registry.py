"""JMeter Worker Registry — the single source of truth for the JMeter pool.

Workers announce themselves at startup and periodically thereafter; the server
never derives a worker address from a prefix or a replica index.

This registry holds only what scheduling needs: worker_id, hostname, endpoint,
port, op_id, last_heartbeat, lifecycle state. Busy/idle is *derived* from
``op_id`` — there is no separate status field to fall out of sync.

**No capability tracking.** Beyond the health state above, nothing about a
worker is recorded. A capability model has no consumer today: every JMeter
worker runs the same image and the scheduler's only question is "is this worker
active and idle?". Adding capability fields now would be the engine/capability
abstraction the JMeter architecture explicitly rules out (ADR-0009 — you cannot
usefully abstract over a set of one), and Implement -> Observe -> Extract says
the shape gets decided after there is something to observe.

Thread safety
-------------
All mutating and reading operations are guarded by a single ``threading.Lock``.
Registration volume is low (one call per worker per heartbeat interval), so a
single lock is not a contention concern.
"""

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.core.mcp_logger import MCPLogger
from app.schemas.tools.jmeter.worker import (
    JMeterWorkerInfo,
    JMeterWorkerState,
    generate_jmeter_worker_id,
)


class JMeterWorkerNotRegisteredError(KeyError):
    """Raised when an operation targets a worker_id the registry doesn't know."""


class JMeterWorkerRegistry:
    """In-memory registry of self-registered JMeter workers.

    Intentionally *not* persisted: workers are ephemeral containers that
    re-register on every start, so registry state rebuilds itself after an
    mcp-server restart. What must survive a restart — queued operations — lives
    in the Operations table instead.
    """

    def __init__(self) -> None:
        self._workers: Dict[str, JMeterWorkerInfo] = {}
        self._lock = threading.Lock()
        self._logger = MCPLogger("JMeterWorkerRegistry")

    # ── Registration / heartbeat ─────────────────────────────────────────

    def register(
        self,
        *,
        worker_id: Optional[str],
        hostname: str,
        endpoint: str,
        port: int,
    ) -> JMeterWorkerInfo:
        """Register a new worker, or re-register an existing one (upsert).

        Re-registration (same ``worker_id`` seen again — e.g. the worker process
        restarted) refreshes hostname/endpoint/port, resets ``last_heartbeat``,
        and brings the worker back to ACTIVE. Any ``op_id`` it held is left
        alone: if that operation really is orphaned, the heartbeat sweep or the
        Runtime finishing it resolves that. A bare register call must never
        silently erase in-flight work bookkeeping.
        """
        resolved_id = worker_id or hostname or generate_jmeter_worker_id()
        now = datetime.now(timezone.utc)

        with self._lock:
            existing = self._workers.get(resolved_id)
            info = JMeterWorkerInfo(
                worker_id=resolved_id,
                hostname=hostname,
                endpoint=endpoint,
                port=port,
                state=JMeterWorkerState.ACTIVE,
                op_id=existing.op_id if existing else None,
                last_heartbeat=now,
                registered_at=existing.registered_at if existing else now,
            )
            self._workers[resolved_id] = info

        self._logger.info(
            "JMeter worker '%s' registered (hostname=%s, endpoint=%s%s)",
            resolved_id,
            hostname,
            endpoint,
            ", re-registration" if existing else "",
        )
        return info

    def heartbeat(self, worker_id: str) -> JMeterWorkerInfo:
        """Record a heartbeat for *worker_id*.

        A worker that heartbeats while OFFLINE (it was reaped for a timeout but
        is in fact alive) self-heals back to ACTIVE, and the next scheduling
        pass considers it again.

        Raises:
            JMeterWorkerNotRegisteredError: if *worker_id* has never registered
                or was explicitly removed. The router turns that into a 404 so
                the worker knows to call register again.
        """
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise JMeterWorkerNotRegisteredError(worker_id)

            updated = info.model_copy(
                update={
                    "last_heartbeat": datetime.now(timezone.utc),
                    "state": (
                        JMeterWorkerState.ACTIVE
                        if info.state == JMeterWorkerState.OFFLINE
                        else info.state
                    ),
                }
            )
            self._workers[worker_id] = updated
            return updated

    # ── Assignment (used by the Pool Manager) ────────────────────────────

    def assign(self, worker_id: str, operation_id: str) -> None:
        """Bind *operation_id* to *worker_id* (the worker becomes busy)."""
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise JMeterWorkerNotRegisteredError(worker_id)
            self._workers[worker_id] = info.model_copy(update={"op_id": operation_id})

    def release(self, worker_id: str, operation_id: Optional[str] = None) -> None:
        """Release *worker_id* back to idle.

        When *operation_id* is given the release only applies if the worker is
        still bound to that exact operation — so a late finishing call cannot
        release a slot that has since been reassigned.

        A worker released while DRAINING has completed its lifecycle
        (Active -> Draining -> finish current operation -> Offline) and
        transitions to OFFLINE here.
        """
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                return  # already gone (e.g. reaped) — nothing to release

            if operation_id is not None and info.op_id != operation_id:
                self._logger.debug(
                    "release: skipped [%s] — owned by %s, not %s",
                    worker_id,
                    info.op_id,
                    operation_id,
                )
                return

            next_state = (
                JMeterWorkerState.OFFLINE
                if info.state == JMeterWorkerState.DRAINING
                else info.state
            )
            self._workers[worker_id] = info.model_copy(
                update={"op_id": None, "state": next_state}
            )

    # ── Maintenance lifecycle ─────────────────────────────────────────────

    def mark_draining(self, worker_id: str) -> JMeterWorkerInfo:
        """Active -> Draining: stop assigning new work, let the current op finish."""
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise JMeterWorkerNotRegisteredError(worker_id)
            updated = info.model_copy(update={"state": JMeterWorkerState.DRAINING})
            self._workers[worker_id] = updated
            return updated

    def remove(self, worker_id: str) -> Optional[str]:
        """Hard-remove a worker (manual or graceful scale-down).

        Returns the ``op_id`` it was executing, if any, so the caller can decide
        how to handle the now-orphaned operation — mirroring the
        heartbeat-timeout path in :meth:`sweep_expired`.
        """
        with self._lock:
            info = self._workers.pop(worker_id, None)
            return info.op_id if info else None

    # ── Heartbeat-timeout sweep ────────────────────────────────────────────

    def sweep_expired(self, timeout_seconds: float) -> List[Tuple[str, str]]:
        """Mark workers Offline whose last heartbeat is older than *timeout_seconds*.

        Returns ``(worker_id, operation_id)`` pairs for workers that *just* went
        Offline while still bound to an operation — the dispatcher marks those
        operations FAILED. Already-Offline workers are not re-reported, so one
        dead worker never produces repeated failures.
        """
        now = datetime.now(timezone.utc)
        orphaned: List[Tuple[str, str]] = []

        with self._lock:
            for worker_id, info in list(self._workers.items()):
                if info.state == JMeterWorkerState.OFFLINE:
                    continue
                age = (now - info.last_heartbeat).total_seconds()
                if age <= timeout_seconds:
                    continue

                self._logger.warning(
                    "JMeter worker '%s' heartbeat timeout (%.0fs > %.0fs) — marking Offline",
                    worker_id,
                    age,
                    timeout_seconds,
                )
                had_op = info.op_id
                self._workers[worker_id] = info.model_copy(
                    update={"state": JMeterWorkerState.OFFLINE, "op_id": None}
                )
                if had_op is not None:
                    orphaned.append((worker_id, had_op))

        return orphaned

    # ── Read access ────────────────────────────────────────────────────────

    def get(self, worker_id: str) -> Optional[JMeterWorkerInfo]:
        """Return one worker's current state, or ``None`` if not registered."""
        with self._lock:
            return self._workers.get(worker_id)

    def list_all(self) -> List[JMeterWorkerInfo]:
        """Return a snapshot of every registered worker, sorted by worker_id.

        The stable order matters: the scheduler rotates over this same ordering
        rather than an arbitrary dict-iteration order, which is what makes
        selection deterministic.
        """
        with self._lock:
            return sorted(self._workers.values(), key=lambda w: w.worker_id)

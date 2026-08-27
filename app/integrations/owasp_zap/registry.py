"""
ZAP Worker Registry — the single source of truth for the pool.

Workers are never derived from a configurable prefix (``prefix-{index}``).
Instead, each worker (a Docker Compose replica today, a Kubernetes pod
tomorrow) announces itself at startup and periodically thereafter:

    Worker Start -> Read HOSTNAME -> Build Endpoint -> POST /workers/register
    ... every N seconds ...        -> POST /workers/heartbeat

This registry only holds what scheduling needs (worker_id, hostname,
endpoint, op_id, last_heartbeat, lifecycle state). Busy/Idle is *derived*
from ``op_id`` — there is no separate status field to fall out of sync.

Thread safety
-------------
All mutating/reading operations are guarded by a single ``threading.Lock``,
mirroring the previous ``OwaspZapPool`` implementation. Registration volume is
low (one call per worker per heartbeat interval), so a single lock is not a
contention concern.
"""

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.core.mcp_logger import MCPLogger
from app.schemas.tools.owasp_zap.worker import (
    ZapWorkerInfo,
    ZapWorkerLifecycleState,
    generate_worker_id,
)


class WorkerNotRegisteredError(KeyError):
    """Raised when an operation targets a worker_id the registry doesn't know."""


class OwaspZapWorkerRegistry:
    """In-memory registry of self-registered ZAP workers.

    This is intentionally *not* persisted: workers are ephemeral (containers /
    pods come and go) and re-register on every start, so registry state is
    naturally rebuilt after an mcp-server restart as soon as workers' next
    heartbeat or (re-)registration call arrives. What *must* survive a
    restart — queued operations — lives in the Operations table instead (see
    ``app/dao``), not here.
    """

    def __init__(self) -> None:
        self._workers: Dict[str, ZapWorkerInfo] = {}
        self._lock = threading.Lock()
        self._logger = MCPLogger("OwaspZapWorkerRegistry")

    # ── Registration / heartbeat ─────────────────────────────────────────

    def register(
        self,
        *,
        worker_id: Optional[str],
        hostname: str,
        endpoint: str,
        port: int,
        version: Optional[str] = None,
    ) -> ZapWorkerInfo:
        """Register a new worker, or re-register an existing one (upsert).

        Re-registration (same ``worker_id`` seen again — e.g. the worker
        process restarted) refreshes ``hostname``/``endpoint``/``port``/
        ``version``, resets ``last_heartbeat``, and brings the worker back to
        ACTIVE. Any ``op_id`` it previously held is intentionally left alone:
        if it was actually orphaned, the heartbeat-timeout sweep (or the
        worker itself finishing/failing that operation) resolves it — a bare
        register call must never silently erase in-flight work bookkeeping.
        """
        resolved_id = worker_id or hostname or generate_worker_id()
        now = datetime.now(timezone.utc)

        with self._lock:
            existing = self._workers.get(resolved_id)
            info = ZapWorkerInfo(
                worker_id=resolved_id,
                hostname=hostname,
                endpoint=endpoint,
                port=port,
                version=version,
                state=ZapWorkerLifecycleState.ACTIVE,
                op_id=existing.op_id if existing else None,
                last_heartbeat=now,
                registered_at=existing.registered_at if existing else now,
            )
            self._workers[resolved_id] = info

        self._logger.info(
            "Worker '%s' registered (hostname=%s, endpoint=%s%s)",
            resolved_id,
            hostname,
            endpoint,
            ", re-registration" if existing else "",
        )
        return info

    def heartbeat(self, worker_id: str) -> ZapWorkerInfo:
        """Record a heartbeat for *worker_id*.

        A worker that heartbeats while OFFLINE (it was reaped for a timeout,
        but is in fact still alive) self-heals back to ACTIVE — the next
        scheduling pass will consider it again.

        Raises:
            WorkerNotRegisteredError: if *worker_id* has never registered (or
                was explicitly removed) — the caller (router) should respond
                in a way that tells the worker to call ``register`` again.
        """
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise WorkerNotRegisteredError(worker_id)

            updated = info.model_copy(
                update={
                    "last_heartbeat": datetime.now(timezone.utc),
                    "state": (
                        ZapWorkerLifecycleState.ACTIVE
                        if info.state == ZapWorkerLifecycleState.OFFLINE
                        else info.state
                    ),
                }
            )
            self._workers[worker_id] = updated
            return updated

    # ── Assignment (used by the Pool Manager) ────────────────────────────

    def assign(self, worker_id: str, operation_id: str) -> None:
        """Bind *operation_id* to *worker_id* (the worker becomes Busy)."""
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise WorkerNotRegisteredError(worker_id)
            self._workers[worker_id] = info.model_copy(update={"op_id": operation_id})

    def release(self, worker_id: str, operation_id: Optional[str] = None) -> None:
        """Release *worker_id* back to Idle.

        When *operation_id* is given, the release is only applied if the
        worker is still bound to that exact operation — this avoids one
        finishing call releasing a slot that has since been reassigned.

        A worker released while DRAINING has completed its lifecycle
        (Active -> Draining -> finish current operation -> Offline) and
        automatically transitions to OFFLINE here.
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
                ZapWorkerLifecycleState.OFFLINE
                if info.state == ZapWorkerLifecycleState.DRAINING
                else info.state
            )
            self._workers[worker_id] = info.model_copy(
                update={"op_id": None, "state": next_state}
            )

    # ── Maintenance lifecycle ─────────────────────────────────────────────

    def mark_draining(self, worker_id: str) -> ZapWorkerInfo:
        """Active -> Draining: stop assigning new work, let the current op finish."""
        with self._lock:
            info = self._workers.get(worker_id)
            if info is None:
                raise WorkerNotRegisteredError(worker_id)
            updated = info.model_copy(update={"state": ZapWorkerLifecycleState.DRAINING})
            self._workers[worker_id] = updated
            return updated

    def remove(self, worker_id: str) -> Optional[str]:
        """Hard-remove a worker from the registry (manual/graceful removal).

        Returns the ``op_id`` it was executing, if any, so the caller can
        decide how to handle the now-orphaned operation (mirrors the
        heartbeat-timeout path in :meth:`sweep_expired`).
        """
        with self._lock:
            info = self._workers.pop(worker_id, None)
            return info.op_id if info else None

    # ── Heartbeat-timeout sweep ────────────────────────────────────────────

    def sweep_expired(self, timeout_seconds: float) -> List[Tuple[str, str]]:
        """Mark workers Offline whose last heartbeat is older than *timeout_seconds*.

        Returns a list of ``(worker_id, operation_id)`` pairs for workers that
        *just* transitioned to Offline while still bound to an operation — the
        caller (dispatcher) is responsible for marking those operations FAILED.
        Already-Offline workers are not re-reported (no repeated failures for
        the same dead worker).
        """
        now = datetime.now(timezone.utc)
        orphaned: List[Tuple[str, str]] = []

        with self._lock:
            for worker_id, info in list(self._workers.items()):
                if info.state == ZapWorkerLifecycleState.OFFLINE:
                    continue
                age = (now - info.last_heartbeat).total_seconds()
                if age <= timeout_seconds:
                    continue

                self._logger.warning(
                    "Worker '%s' heartbeat timeout (%.0fs > %.0fs) — marking Offline",
                    worker_id,
                    age,
                    timeout_seconds,
                )
                had_op = info.op_id
                self._workers[worker_id] = info.model_copy(
                    update={"state": ZapWorkerLifecycleState.OFFLINE, "op_id": None}
                )
                if had_op is not None:
                    orphaned.append((worker_id, had_op))

        return orphaned

    # ── Read access ────────────────────────────────────────────────────────

    def get(self, worker_id: str) -> Optional[ZapWorkerInfo]:
        with self._lock:
            return self._workers.get(worker_id)

    def list_all(self) -> List[ZapWorkerInfo]:
        """Return a snapshot of every registered worker, sorted by worker_id.

        The stable sort order matters: the scheduler rotates fairly over this
        same ordering rather than an arbitrary dict-iteration order.
        """
        with self._lock:
            return sorted(self._workers.values(), key=lambda w: w.worker_id)

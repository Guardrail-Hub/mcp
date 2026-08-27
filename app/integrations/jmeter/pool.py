"""JMeter Pool Manager.

Assigns queued operations to registered JMeter workers. It never generates a
worker address — every assignment comes from the Worker Registry, which holds
whatever a worker reported when it registered.

Split of responsibilities:

* :class:`~app.integrations.jmeter.registry.JMeterWorkerRegistry` — *who* is
  registered, their endpoint, and busy/idle (``op_id``), plus the maintenance
  lifecycle (drain/offline/remove).
* :class:`~app.integrations.jmeter.scheduler.JMeterWorkerScheduler` — *which*
  eligible worker to pick next.
* ``JMeterWorkerPoolManager`` (this class) — ties the two together for the
  dispatcher: try to assign one operation, hand back a client bound to the
  chosen worker, release when done.

This class never polls or blocks waiting for a slot. That loop lives in the
dispatcher, which re-tries queued operations from the Operations table on its
own schedule.
"""

import threading
from typing import Dict, Optional

from app.core.mcp_logger import MCPLogger
from app.integrations.jmeter.registry import JMeterWorkerRegistry
from app.integrations.jmeter.scheduler import JMeterWorkerScheduler
from app.integrations.jmeter.worker_client import JMeterWorkerClient
from app.schemas.tools.jmeter.worker import JMeterWorkerInfo


class JMeterWorkerPoolManager:
    """Coordinates worker selection and client access for the dispatcher."""

    def __init__(
        self,
        registry: JMeterWorkerRegistry,
        scheduler: JMeterWorkerScheduler,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        """
        Args:
            registry: Source of truth for the registered pool.
            scheduler: Strategy that picks the next worker.
            request_timeout_seconds: Transport timeout applied to each client
                built by :meth:`get_client`.
        """
        self.registry = registry
        self.scheduler = scheduler
        self._request_timeout_seconds = request_timeout_seconds

        self._clients: Dict[str, JMeterWorkerClient] = {}
        self._clients_lock = threading.Lock()
        self._logger = MCPLogger("JMeterWorkerPoolManager")

    # ── Assignment ─────────────────────────────────────────────────────────

    def try_assign(self, operation_id: str) -> Optional[JMeterWorkerInfo]:
        """Try to assign *operation_id* to one eligible worker.

        Availability is validated by the scheduler against the registry's own
        state — a worker is eligible only when it is idle *and* ACTIVE
        (``JMeterWorkerInfo.is_schedulable``). No network probe happens here:
        liveness is what heartbeats already answer, and probing on every
        assignment would add a failure mode to the scheduling path.

        Returns the assigned worker with ``op_id`` already set, or ``None`` when
        nothing is eligible — in which case the operation simply stays
        ``QUEUED`` and the dispatcher retries it next tick.
        """
        candidates = self.registry.list_all()
        chosen = self.scheduler.select(candidates)
        if chosen is None:
            return None

        self.registry.assign(chosen.worker_id, operation_id)
        self._logger.info(
            "Assigned operation '%s' -> JMeter worker '%s' (%s)",
            operation_id,
            chosen.worker_id,
            chosen.endpoint,
        )
        return self.registry.get(chosen.worker_id) or chosen

    def release_worker(self, worker_id: str, operation_id: str) -> None:
        """Release a worker back to idle once *operation_id* has finished."""
        self.registry.release(worker_id, operation_id)

    # ── Client access ──────────────────────────────────────────────────────

    def get_client(self, worker: JMeterWorkerInfo) -> JMeterWorkerClient:
        """Return a cached :class:`JMeterWorkerClient` bound to *worker*.

        Cached per ``worker_id`` and rebuilt when the worker re-registers with a
        different endpoint (e.g. it restarted at a new address).
        """
        with self._clients_lock:
            cached = self._clients.get(worker.worker_id)
            if cached is not None and cached.endpoint == worker.endpoint:
                return cached

            client = JMeterWorkerClient(
                endpoint=worker.endpoint,
                worker_id=worker.worker_id,
                timeout_seconds=self._request_timeout_seconds,
            )
            self._clients[worker.worker_id] = client
            return client

    def forget_client(self, worker_id: str) -> None:
        """Drop a cached client, e.g. after a worker is removed from the registry."""
        with self._clients_lock:
            self._clients.pop(worker_id, None)

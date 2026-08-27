"""
ZAP Pool Manager.

Assigns queued operations to registered ZAP workers. It never generates a
worker address (no ``http://prefix-1``, no ``http://prefix-2``) — every
assignment comes from the Worker Registry, which holds whatever a worker
reported at registration.

Split of responsibilities:

* :class:`~app.integrations.owasp_zap.registry.OwaspZapWorkerRegistry` — the
  source of truth for *who* is registered, their endpoint, and busy/idle
  (``op_id``). Also handles maintenance lifecycle (drain/offline/remove).
* :class:`~app.integrations.owasp_zap.scheduler.WorkerScheduler` — *which*
  idle worker to pick next (Round Robin by default, swappable).
* ``OwaspZapPoolManager`` (this class) — ties the two together for the
  dispatcher: try to assign one operation, hand back a ready ``ZapClient``,
  release when done.

This class does not poll or block waiting for a slot — that loop lives in the
dispatcher (``app/services/tools/owasp_zap/execution/dispatcher.py``), which re-tries
queued operations (persisted in the Operations table) on its own schedule.
"""

import threading
from typing import Dict, Optional

from app.core.mcp_logger import MCPLogger
from app.integrations.owasp_zap.client import ZapClient
from app.integrations.owasp_zap.registry import OwaspZapWorkerRegistry
from app.integrations.owasp_zap.scheduler import WorkerScheduler
from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo


class OwaspZapPoolManager:
    """Coordinates worker selection and ``ZapClient`` access for the dispatcher."""

    def __init__(
        self,
        registry: OwaspZapWorkerRegistry,
        scheduler: WorkerScheduler,
        api_key: Optional[str] = None,
    ) -> None:
        self.registry = registry
        self.scheduler = scheduler
        self._api_key = api_key

        self._clients: Dict[str, ZapClient] = {}
        self._clients_lock = threading.Lock()
        self._logger = MCPLogger("OwaspZapPoolManager")

    # ── Assignment ─────────────────────────────────────────────────────────

    def try_assign(self, operation_id: str) -> Optional[ZapWorkerInfo]:
        """Try to assign *operation_id* to one eligible worker.

        Returns the assigned :class:`ZapWorkerInfo` (with ``op_id`` already
        set) or ``None`` if no worker is currently idle and Active — in which
        case the operation simply stays ``QUEUED`` and the dispatcher will
        retry it on the next poll.
        """
        candidates = self.registry.list_all()
        chosen = self.scheduler.select(candidates)
        if chosen is None:
            return None

        self.registry.assign(chosen.worker_id, operation_id)
        self._logger.info(
            "Assigned operation '%s' -> worker '%s' (%s)",
            operation_id,
            chosen.worker_id,
            chosen.endpoint,
        )
        return self.registry.get(chosen.worker_id) or chosen

    def release_worker(self, worker_id: str, operation_id: str) -> None:
        """Release a worker back to Idle once *operation_id* has finished."""
        self.registry.release(worker_id, operation_id)

    # ── Client access ──────────────────────────────────────────────────────

    def get_client(self, worker: ZapWorkerInfo) -> ZapClient:
        """Return a (cached) :class:`ZapClient` bound to *worker*'s endpoint.

        Cached per ``worker_id`` and rebuilt if the worker re-registered with
        a different endpoint (e.g. it restarted and picked up a new address).
        """
        with self._clients_lock:
            cached = self._clients.get(worker.worker_id)
            if cached is not None and cached.proxy_url == worker.endpoint:
                return cached

            client = ZapClient(api_url=worker.endpoint, api_key=self._api_key)
            self._clients[worker.worker_id] = client
            return client

    def forget_client(self, worker_id: str) -> None:
        """Drop a cached client, e.g. after a worker is removed from the registry."""
        with self._clients_lock:
            self._clients.pop(worker_id, None)

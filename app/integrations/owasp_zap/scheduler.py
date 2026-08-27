"""
Worker scheduling strategies for the ZAP Pool Manager.

Isolated from the Pool Manager on purpose (requirement: "the scheduling
algorithm should be isolated so it can be replaced later") — swapping Round
Robin for e.g. least-recently-used or weighted-by-capacity later is a matter
of implementing :class:`WorkerScheduler` and passing it to
``OwaspZapPoolManager``, with no change to the Pool Manager itself.
"""

import threading
from typing import List, Optional, Protocol

from app.schemas.tools.owasp_zap.worker import ZapWorkerInfo


class WorkerScheduler(Protocol):
    """Strategy interface: pick one worker to run the next operation."""

    def select(self, workers: List[ZapWorkerInfo]) -> Optional[ZapWorkerInfo]:
        """Return the worker to assign next, or ``None`` if none is eligible.

        Args:
            workers: The full, stably-ordered snapshot of *registered*
                workers (idle, busy, draining, offline — all of them). The
                strategy decides eligibility itself so it can see the whole
                pool, not just whoever happens to be idle right now.
        """
        ...


class RoundRobinScheduler:
    """Fair rotation across the full registered worker set.

    Deliberately does *not* just take "the first idle worker": it keeps a
    persistent cursor over the stable (sorted-by-worker_id) ordering of every
    known worker and, on each call, scans forward from the cursor for the
    first worker that is both idle and Active, wrapping around once. This
    spreads load evenly across the pool over time instead of always favoring
    whichever worker happens to sort first.
    """

    def __init__(self) -> None:
        self._cursor = 0
        self._lock = threading.Lock()

    def select(self, workers: List[ZapWorkerInfo]) -> Optional[ZapWorkerInfo]:
        if not workers:
            return None

        with self._lock:
            n = len(workers)
            for step in range(n):
                idx = (self._cursor + step) % n
                candidate = workers[idx]
                if candidate.is_schedulable:
                    self._cursor = (idx + 1) % n
                    return candidate

            # Nobody eligible right now; keep the cursor where it was so the
            # next call resumes rotation instead of always restarting at 0.
            return None

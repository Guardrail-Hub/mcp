"""Worker scheduling strategies for the JMeter Pool Manager.

Isolated from the Pool Manager on purpose: replacing round-robin with a
different policy later means implementing :class:`JMeterWorkerScheduler` and
passing it to ``JMeterWorkerPoolManager``, with no change to the Pool Manager
itself. That Protocol is the extension point — the only one this tier has, and
it exists because the strategy is genuinely swappable, not in anticipation of a
second engine.

Selection is **deterministic**: the registry hands over a stably-sorted
snapshot, and the strategy's choice is a pure function of that snapshot plus
its own cursor. The same pool in the same state always yields the same pick.
"""

import threading
from typing import List, Optional, Protocol

from app.schemas.tools.jmeter.worker import JMeterWorkerInfo


class JMeterWorkerScheduler(Protocol):
    """Strategy interface: pick one worker to run the next operation."""

    def select(self, workers: List[JMeterWorkerInfo]) -> Optional[JMeterWorkerInfo]:
        """Return the worker to assign next, or ``None`` if none is eligible.

        Args:
            workers: The full, stably-ordered snapshot of *registered* workers
                — idle, busy, draining and offline alike. The strategy decides
                eligibility itself so it can see the whole pool rather than
                only whoever happens to be idle at this instant.
        """
        ...


class RoundRobinJMeterWorkerScheduler:
    """Fair rotation across the full registered worker set.

    Deliberately does not simply take "the first idle worker": it keeps a
    persistent cursor over the stable ordering of every known worker and, on
    each call, scans forward from the cursor for the first worker that is both
    idle and Active, wrapping around once. Over time that spreads load evenly
    instead of always favouring whichever worker sorts first — which matters
    more for load generation than for scanning, since a JMeter worker's
    resources stay committed for the whole run.
    """

    def __init__(self) -> None:
        self._cursor = 0
        self._lock = threading.Lock()

    def select(self, workers: List[JMeterWorkerInfo]) -> Optional[JMeterWorkerInfo]:
        if not workers:
            return None

        with self._lock:
            total = len(workers)
            for step in range(total):
                index = (self._cursor + step) % total
                candidate = workers[index]
                if candidate.is_schedulable:
                    self._cursor = (index + 1) % total
                    return candidate

            # Nobody eligible right now; leave the cursor where it is so the
            # next call resumes the rotation instead of restarting at 0.
            return None

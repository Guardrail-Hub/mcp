"""How an operation is executed — independent of *what* it is.

``ExecutionStrategy`` (HOW) is kept deliberately separate from
``ZapOperationType`` (WHAT): two different operation types can share a strategy
(``API_SCAN`` and ``API_SCENARIO`` are both ``SEQUENTIAL`` — a single scan bound
to one worker), and the two concepts evolve independently.

The Dispatcher reads exactly one thing off a strategy: :attr:`requires_worker`.
That single generic flag is what lets the Dispatcher route every operation
without ever learning a tool's identity:

* worker-bound strategies claim a ZAP worker from the pool and run a scan on it;
* orchestration strategies (``FAN_OUT`` / ``FAN_IN``) run WITHOUT ever occupying
  a worker — they spawn and/or aggregate child operations. This is what keeps
  an ``api_suite`` orchestrator from holding a worker slot while its child
  scans run (workers only ever execute real scan operations).
"""

from enum import Enum


class ExecutionStrategy(str, Enum):
    """How work runs. Orthogonal to ZapOperationType."""

    # Worker-bound: one operation runs on one assigned ZAP worker.
    SEQUENTIAL = "sequential"  # ordered work on one worker (api_scan, api_scenario)
    PARALLEL = "parallel"      # one worker, internally parallelised (reserved)
    BATCH = "batch"            # reserved for future batching semantics

    # Orchestration: no worker is ever claimed.
    FAN_OUT = "fan_out"        # spawns child operations (api_suite)
    FAN_IN = "fan_in"          # aggregates child results (api_suite)

    @property
    def requires_worker(self) -> bool:
        """Whether the Dispatcher must assign a ZAP worker before executing.

        Orchestration strategies never hold a pool worker; every other strategy
        is worker-bound. This is the ONLY property the Dispatcher inspects, so
        routing stays fully generic.
        """
        return self not in (ExecutionStrategy.FAN_OUT, ExecutionStrategy.FAN_IN)

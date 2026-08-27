"""OperationPhase — the canonical lifecycle vocabulary (Decision 0008 section 3.2).

The single definition of the stages an Operation moves through. Replaces the
former duplicated pair ``OperationStatus`` (a ZAP tool schema) and
``LifecyclePhase`` (the notification package), which had identical members and
identical string values.

Invariants (Decision 0008):
    I5 — the member set is closed and changes only additively.
    I6 — ``is_terminal`` is total: every phase answers it.
    I7 — string values are stable, because they are persisted.
"""

from enum import Enum


class OperationPhase(str, Enum):
    """The stage of an Operation's life.

    ``QUEUED`` operations are the persisted work queue itself — there is no
    separate in-memory waiting queue. Terminal phases are absorbing: an
    operation that reaches one never leaves it (invariant I3).

        QUEUED -> RUNNING -> COMPLETED | FAILED | CANCELLED
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Whether this phase ends the operation (no further updates follow)."""
        return self in _TERMINAL_PHASES


# Defined after the class so membership is evaluated once, not per call.
_TERMINAL_PHASES = frozenset(
    {OperationPhase.COMPLETED, OperationPhase.FAILED, OperationPhase.CANCELLED}
)

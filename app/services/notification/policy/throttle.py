"""Progress-update throttling.

Keeps progress notifications from spamming a channel. A progress snapshot is
worth delivering when *any* of these is true, whichever occurs first:

- the stage changed, OR
- progress advanced by at least ``min_percent_step`` (default 10%), OR
- at least ``min_interval_seconds`` (default 30s) elapsed since the last emit.

Terminal/lifecycle transitions are NOT throttled — the caller delivers those
unconditionally; this gate is only consulted for intermediate progress.

The clock is injectable so the policy is unit-testable without real waiting.
"""

from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Optional

DEFAULT_MIN_PERCENT_STEP = 10
DEFAULT_MIN_INTERVAL_SECONDS = 30.0


@dataclass
class ProgressThrottle:
    """Per-operation decision on whether a progress snapshot should be emitted.

    One instance tracks one operation. It is stateful: each accepted snapshot
    updates the baseline the next one is compared against.

    Attributes:
        min_percent_step: Minimum progress delta (percentage points) that on its
            own justifies an emit.
        min_interval_seconds: Minimum elapsed time that on its own justifies an
            emit.
        clock: Monotonic time source, in seconds. Injectable for tests.
    """

    min_percent_step: int = DEFAULT_MIN_PERCENT_STEP
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    clock: Callable[[], float] = monotonic
    _last_stage: Optional[str] = field(default=None, init=False)
    _last_progress: Optional[int] = field(default=None, init=False)
    _last_emit_at: Optional[float] = field(default=None, init=False)

    def should_emit(self, stage: Optional[str], progress: Optional[int]) -> bool:
        """Return whether a snapshot with *stage*/*progress* should be delivered.

        The first snapshot always passes. Subsequent ones pass on a stage change,
        a progress jump of at least ``min_percent_step``, or ``min_interval_seconds``
        elapsed — whichever comes first. When it returns ``True`` the internal
        baseline is advanced, so callers should only call this once per snapshot.
        """
        now = self.clock()

        if self._last_emit_at is None:
            return self._accept(stage, progress, now)

        if stage != self._last_stage:
            return self._accept(stage, progress, now)

        if (
            progress is not None
            and self._last_progress is not None
            and progress - self._last_progress >= self.min_percent_step
        ):
            return self._accept(stage, progress, now)

        if now - self._last_emit_at >= self.min_interval_seconds:
            return self._accept(stage, progress, now)

        return False

    def _accept(self, stage: Optional[str], progress: Optional[int], now: float) -> bool:
        self._last_stage = stage
        self._last_progress = progress
        self._last_emit_at = now
        return True

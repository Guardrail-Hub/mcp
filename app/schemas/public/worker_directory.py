"""The operator-facing view of every worker, across engines.

**This is a projection, not a model.** It is built on read from whatever each
engine's registry already holds, and nothing persists it. ``ZapWorkerInfo`` and
``JMeterWorkerInfo`` remain the source of truth for their own pools and are
unchanged — deliberately, because the two engines' worker concepts are only
*similar*, and a shared base class would be an abstraction over a resemblance
rather than over a requirement (the mistake ADR-0009 rejected).

The consequence to keep in mind: adding a field here does not make it exist.
Every field below is one both registries already report, or is derived from
those. A field only one engine has would have to be optional and would be
silently absent half the time, which is worse than not offering it.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UnifiedWorkerInfo(BaseModel):
    """One worker, from any engine's registry."""

    worker_type: str = Field(
        ...,
        description=(
            "Which engine's pool this worker belongs to — 'zap' or 'jmeter'. "
            "Carried in the payload rather than implied by the endpoint, so a "
            "stored or forwarded record stays self-describing."
        ),
        examples=["zap", "jmeter"],
    )
    worker_id: str = Field(..., description="Registered identity, unique within its pool.")
    hostname: str = Field(..., description="Container hostname the worker registered under.")
    endpoint: str = Field(..., description="Base URL the server uses to reach it.")
    port: int = Field(..., description="Port the worker's own API listens on.")
    state: str = Field(
        ...,
        description="Lifecycle: 'active', 'draining' or 'offline'.",
        examples=["active"],
    )
    op_id: Optional[str] = Field(
        None, description="Operation currently executing here, if any."
    )
    available: bool = Field(
        ...,
        description=(
            "True when this worker could take new work right now: idle "
            "(op_id is null) AND state is 'active'. Additive convenience — "
            "op_id and state are both still reported and remain authoritative."
        ),
    )
    last_heartbeat: datetime = Field(
        ..., description="When the worker last reported in."
    )

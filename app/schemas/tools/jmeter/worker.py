"""JMeter worker registry schemas.

Mirrors the ZAP worker registry *pattern* with JMeter-specific types. Nothing
here imports from ``app/schemas/tools/owasp_zap/`` — the ZAP models are typed on
ZAP's own worker and reusing them would create the cross-tool coupling ADR-0011
and the JMeter design deliberately avoid. The duplication is the "Implement"
step of Implement -> Observe -> Extract.

A JMeter worker is long-lived (so heartbeats are meaningful); only the JMeter
process it launches per operation is short-lived. The protocol matches ZAP's:

    Worker start -> read HOSTNAME -> build endpoint -> POST /jmeter-workers/register
    ... every N seconds ...        -> POST /jmeter-workers/heartbeat
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class JMeterWorkerState(str, Enum):
    """Lifecycle of a registered worker, independent of its busy/idle state.

    Busy/idle is derived (``op_id is not None``); this tracks whether the worker
    may receive *new* work at all.

        ACTIVE   -> may receive new operations (subject to being idle).
        DRAINING -> finishing its current operation, then goes OFFLINE.
        OFFLINE  -> unreachable (heartbeat timeout) or drained; never assigned.
    """

    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"


class JMeterWorkerInfo(BaseModel):
    """Scheduling-facing view of a single registered JMeter worker.

    Holds only what scheduling needs. There is deliberately no capability or
    engine-version field — see the module note in
    ``app/integrations/jmeter/registry.py``.
    """

    worker_id: str
    hostname: str
    endpoint: str = Field(..., description="Base URL the server uses to reach the worker.")
    port: int
    # No engine-version field, deliberately: the JMeter version is a property of
    # the worker's Docker image and is not reported, selected, or recorded here.
    state: JMeterWorkerState = JMeterWorkerState.ACTIVE
    op_id: Optional[str] = Field(
        None, description="Operation currently executing on this worker, if any."
    )
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_idle(self) -> bool:
        """Whether this worker is currently free to take an operation."""
        return self.op_id is None

    @property
    def is_schedulable(self) -> bool:
        """Eligible to receive a new operation: idle and not draining/offline."""
        return self.is_idle and self.state == JMeterWorkerState.ACTIVE


class JMeterWorkerRegisterRequest(BaseModel):
    """Body a worker POSTs to ``/jmeter-workers/register`` on startup."""

    worker_id: Optional[str] = Field(
        None,
        description="Stable worker identifier. Defaults to the container HOSTNAME. "
        "If omitted entirely, the server generates one.",
        examples=["guardrail-hub-jmeter-1"],
    )
    hostname: str = Field(
        ...,
        description="DNS-resolvable hostname of the worker inside the deployment network.",
        examples=["guardrail-hub-jmeter-1"],
    )
    endpoint: str = Field(
        ...,
        description="Full base URL of the worker's agent API, built by the worker itself "
        "from its own hostname and port. The server never derives this from a prefix.",
        examples=["http://guardrail-hub-jmeter-1:8090"],
    )
    port: int = Field(..., description="Port the worker agent listens on.", examples=[8090])


class JMeterWorkerRegisterResponse(BaseModel):
    worker_id: str
    hostname: str
    endpoint: str
    state: JMeterWorkerState
    registered_at: datetime


class JMeterWorkerHeartbeatRequest(BaseModel):
    """Body a worker POSTs periodically to ``/jmeter-workers/heartbeat``."""

    worker_id: str = Field(..., description="The worker_id returned at registration.")


class JMeterWorkerHeartbeatResponse(BaseModel):
    worker_id: str
    state: JMeterWorkerState
    last_heartbeat: datetime


def generate_jmeter_worker_id() -> str:
    """Fallback worker_id when a worker registers without one or a hostname."""
    return f"jmeter-worker-{uuid4().hex[:12]}"

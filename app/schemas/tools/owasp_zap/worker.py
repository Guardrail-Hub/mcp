"""
ZAP worker registry schemas.

These models back the self-registration / heartbeat protocol that replaced
prefix-based worker discovery (``prefix-{index}``). A ZAP worker is anything
that speaks the ZAP REST API and announces itself to the pool at runtime —
a Docker Compose replica today, a Kubernetes pod tomorrow.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ZapWorkerLifecycleState(str, Enum):
    """Lifecycle of a registered worker, independent of its busy/idle state.

    Busy/idle is derived (``op_id is not None``); this enum tracks whether the
    worker may receive *new* work at all.

        ACTIVE   -> may receive new operations (subject to being idle).
        DRAINING -> finishing its current operation, then auto-transitions to
                    OFFLINE once released (see registry.release()).
        OFFLINE  -> unreachable (heartbeat timeout) or drained; never assigned.
    """

    ACTIVE = "active"
    DRAINING = "draining"
    OFFLINE = "offline"


class ZapWorkerRegisterRequest(BaseModel):
    """Body a worker POSTs to ``/workers/register`` on startup."""

    worker_id: Optional[str] = Field(
        None,
        description="Stable worker identifier. Defaults to the container HOSTNAME. "
        "If omitted entirely, the server generates a UUID.",
        examples=["guardrail-hub-zap-1"],
    )
    hostname: str = Field(
        ...,
        description="DNS-resolvable hostname of the worker inside the deployment network "
        "(Docker service/container name today, Pod DNS name under Kubernetes).",
        examples=["guardrail-hub-zap-1"],
    )
    endpoint: str = Field(
        ...,
        description="Full base URL of the worker's ZAP REST API, built by the worker itself "
        "from its own hostname and port. The server never derives this from a prefix.",
        examples=["http://guardrail-hub-zap-1:8080"],
    )
    port: int = Field(..., description="Port the ZAP REST API listens on.", examples=[8080])
    version: Optional[str] = Field(
        None, description="Optional ZAP / image version string, for observability only."
    )


class ZapWorkerRegisterResponse(BaseModel):
    worker_id: str
    hostname: str
    endpoint: str
    state: ZapWorkerLifecycleState
    registered_at: datetime


class ZapWorkerHeartbeatRequest(BaseModel):
    """Body a worker POSTs periodically to ``/workers/heartbeat``."""

    worker_id: str = Field(..., description="The worker_id returned at registration.")


class ZapWorkerHeartbeatResponse(BaseModel):
    worker_id: str
    state: ZapWorkerLifecycleState
    last_heartbeat: datetime


class ZapWorkerInfo(BaseModel):
    """Scheduling-facing view of a single registered worker.

    Contains only what the Pool Manager / scheduler need. ``op_id`` is the
    single source of truth for busy/idle — there is no separate status field.
    """

    worker_id: str
    hostname: str
    endpoint: str
    port: int
    version: Optional[str] = None
    state: ZapWorkerLifecycleState = ZapWorkerLifecycleState.ACTIVE
    op_id: Optional[str] = None
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_idle(self) -> bool:
        return self.op_id is None

    @property
    def is_schedulable(self) -> bool:
        """Eligible to receive a new operation: idle and not draining/offline."""
        return self.is_idle and self.state == ZapWorkerLifecycleState.ACTIVE


def generate_worker_id() -> str:
    """Fallback worker_id when a worker registers without one or a hostname."""
    return f"zap-worker-{uuid4().hex[:12]}"

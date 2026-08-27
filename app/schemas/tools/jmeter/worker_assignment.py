"""The Dispatcher <-> Worker communication contract.

Four messages, in the order they occur:

1. :class:`JMeterAssignmentRequest`  — server -> worker: run this operation,
   here is its payload.
2. :class:`JMeterAssignmentAck`      — worker -> server: accepted, or refused
   with a reason.
3. :class:`JMeterExecutionStateReport` — worker -> server: where the run is now
   (polled by the Runtime once execution exists).
4. The same report carrying :attr:`JMeterExecutionState.FAILED` and a message —
   failure reporting is not a separate message type, it is a terminal state.

**Who writes lifecycle to the database.** Nothing here does. The worker reports
*its own* view of the run; translating that into ``OperationService`` calls
belongs to ``JMeterRuntime``, which the accepted design names as the sole owner
of lifecycle persistence for JMeter operations
(``architecture/jmeter-engine/runtime-responsibility.md`` §3). That is why the
worker pushes nothing to the server: it answers when asked.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.tools.jmeter.run_jmeter_test import JMeterTestRequest


class JMeterExecutionState(str, Enum):
    """Where a worker's run of one operation currently is.

    Distinct from ``OperationPhase``, deliberately: this is the *worker's*
    local view of a subprocess, not the operation's persisted lifecycle. The
    Runtime maps one onto the other; nothing else should.
    """

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class JMeterAssignmentRequest(BaseModel):
    """Server -> worker: execute this operation.

    Carries the whole validated request rather than an id to look up, so the
    worker needs no database access — consistent with the worker owning no
    persistence at all.
    """

    operation_id: str = Field(..., description="Operation this assignment belongs to.")
    request: JMeterTestRequest = Field(
        ..., description="The validated load-test request to execute."
    )
    jmx_plan: Optional[str] = Field(
        None,
        description=(
            "The test plan to run, already generated and validated by the "
            "Runtime. The worker executes exactly this and never builds or "
            "checks a plan of its own — validation is an authorization "
            "decision, and authorization does not happen on the worker."
        ),
    )


class JMeterAssignmentAck(BaseModel):
    """Worker -> server: whether the assignment was taken.

    A refusal is not an error: a worker that is already busy, draining, or
    otherwise unable to start says so, and the operation returns to the queue
    for the next poll tick rather than failing.
    """

    operation_id: str
    worker_id: str
    accepted: bool = Field(..., description="False means the worker declined the work.")
    message: Optional[str] = Field(
        None, description="Why it was declined, when it was."
    )


class JMeterExecutionStateReport(BaseModel):
    """Worker -> server: the current state of one assigned run."""

    operation_id: str
    worker_id: str
    state: JMeterExecutionState
    message: Optional[str] = Field(
        None, description="Detail for the current state; the reason when FAILED."
    )
    workspace: Optional[str] = Field(
        None,
        description=(
            "Directory the run wrote its artifacts into, once it has produced "
            "any. The Runtime derives the artifact paths from it; the file "
            "names inside are fixed by JMeterProcessRunner."
        ),
    )

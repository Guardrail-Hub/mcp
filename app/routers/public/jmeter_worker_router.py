"""JMeter worker registration / heartbeat / maintenance endpoints.

Not MCP tools — these are called by JMeter worker containers themselves, not by
an LLM client, so they are mounted directly on the app (like ``health_router``)
rather than through ``tools_router`` / ``MCP_OPERATIONS``.

    Worker start -> read HOSTNAME -> build endpoint -> POST /jmeter-workers/register
    ... every N seconds ...        -> POST /jmeter-workers/heartbeat

Mounted under ``/jmeter-workers`` rather than ZAP's ``/workers`` so the two
pools have separate, unambiguous surfaces: a worker cannot cross-register into
the wrong pool even by misconfiguration, because the path itself names the
engine.

This router is a thin HTTP adapter. All worker-lifecycle logic — including
failing an operation orphaned by a removed worker — lives in
:class:`app.services.tools.jmeter.jmeter_worker_service.JMeterWorkerService`,
which owns the Worker Registry and the Operations DAO. The router only maps
requests and responses and translates domain errors into status codes.
"""

from fastapi import APIRouter, HTTPException

from app.integrations.jmeter.registry import JMeterWorkerNotRegisteredError
from app.schemas.tools.jmeter.worker import (
    JMeterWorkerHeartbeatRequest,
    JMeterWorkerHeartbeatResponse,
    JMeterWorkerInfo,
    JMeterWorkerRegisterRequest,
    JMeterWorkerRegisterResponse,
)
from app.services.tools.jmeter.jmeter_worker_service import JMeterWorkerService

jmeter_worker_router = APIRouter(prefix="/jmeter-workers", tags=["JMeter Workers"])
_service = JMeterWorkerService()


@jmeter_worker_router.post("/register", response_model=JMeterWorkerRegisterResponse)
def register_jmeter_worker(
    request: JMeterWorkerRegisterRequest,
) -> JMeterWorkerRegisterResponse:
    """Register a JMeter worker (called once at startup, safe to repeat)."""
    info = _service.register(
        worker_id=request.worker_id,
        hostname=request.hostname,
        endpoint=request.endpoint,
        port=request.port,
    )
    return JMeterWorkerRegisterResponse(
        worker_id=info.worker_id,
        hostname=info.hostname,
        endpoint=info.endpoint,
        state=info.state,
        registered_at=info.registered_at,
    )


@jmeter_worker_router.post("/heartbeat", response_model=JMeterWorkerHeartbeatResponse)
def jmeter_worker_heartbeat(
    request: JMeterWorkerHeartbeatRequest,
) -> JMeterWorkerHeartbeatResponse:
    """Record a heartbeat for a previously-registered worker.

    Returns 404 when ``worker_id`` isn't known — never registered, removed, or
    reaped for a heartbeat timeout and not yet self-healed. The worker's
    heartbeat loop should treat that as a signal to call ``/register`` again.
    """
    try:
        info = _service.heartbeat(request.worker_id)
    except JMeterWorkerNotRegisteredError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"JMeter worker '{request.worker_id}' is not registered — "
                "call /jmeter-workers/register again."
            ),
        ) from exc

    return JMeterWorkerHeartbeatResponse(
        worker_id=info.worker_id, state=info.state, last_heartbeat=info.last_heartbeat
    )


@jmeter_worker_router.get("", response_model=list[JMeterWorkerInfo])
def list_jmeter_workers() -> list[JMeterWorkerInfo]:
    """List every registered JMeter worker (observability / debugging only)."""
    return _service.list_workers()


@jmeter_worker_router.post("/{worker_id}/drain", response_model=JMeterWorkerInfo)
def drain_jmeter_worker(worker_id: str) -> JMeterWorkerInfo:
    """Begin graceful maintenance: Active -> Draining.

    A draining worker finishes its current operation (if any) but receives no
    new ones, then becomes Offline once released.
    """
    try:
        return _service.drain(worker_id)
    except JMeterWorkerNotRegisteredError as exc:
        raise HTTPException(
            status_code=404, detail=f"JMeter worker '{worker_id}' not found"
        ) from exc


@jmeter_worker_router.delete("/{worker_id}")
def remove_jmeter_worker(worker_id: str) -> dict:
    """Hard-remove a worker from the registry (manual / scale-down cleanup).

    Unlike the heartbeat-timeout path this is immediate — intended for a
    deliberate scale-down rather than a crash. If the worker was executing an
    operation, that operation is marked FAILED (the same "no automatic retry"
    rule as the timeout path): a worker removed from the registry can never be
    assigned to again, so leaving the operation RUNNING would strand it.
    """
    orphaned_op_id = _service.remove(worker_id)
    return {
        "worker_id": worker_id,
        "removed": True,
        "orphaned_operation_id": orphaned_op_id,
    }

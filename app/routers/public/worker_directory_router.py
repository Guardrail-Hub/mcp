"""``GET /workers`` — every engine's workers in one list.

Not an MCP tool: this is operator plumbing, like ``health_router``, so it is
mounted directly on the app and stays out of ``MCP_OPERATIONS``.

**This path used to mean "ZAP workers".** ``zap_worker_router`` owned the whole
``/workers`` prefix, so ``GET /workers`` returned ``list[ZapWorkerInfo]``. It now
returns every engine's workers, each tagged with ``worker_type``. ZAP's own list
did not disappear — it moved to ``GET /zap-workers``, alongside the rest of the
ZAP worker surface, which is now reachable under both prefixes. The write paths
a worker container uses (``POST /workers/register``, ``/workers/heartbeat``,
``DELETE /workers/{id}``) are untouched, so no running worker needs rebuilding.
"""

from fastapi import APIRouter

from app.schemas.public.worker_directory import UnifiedWorkerInfo
from app.services.public.worker_directory_service import WorkerDirectoryService

worker_directory_router = APIRouter(prefix="/workers", tags=["Workers"])
_service = WorkerDirectoryService()


@worker_directory_router.get("", response_model=list[UnifiedWorkerInfo])
def list_all_workers() -> list[UnifiedWorkerInfo]:
    """List every registered worker across all engines.

    Read-only and non-authoritative: each engine's registry remains the source
    of truth for its own pool, and nothing here changes any worker's state.

    Sorted by `worker_type`, then `hostname`, then `worker_id`. Each entry
    carries `worker_type` ('zap' or 'jmeter') and `available` — true when the
    worker is idle (`op_id` is null) and its `state` is 'active'.
    """
    return _service.list_workers()

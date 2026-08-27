"""
Public history router — HTTP layer only.

All business logic lives in :class:`app.services.public.history_service.HistoryService`.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.constants.batch import BatchType
from app.services.public import HistoryService

history_router = APIRouter(prefix="/history", tags=["History"])
_service = HistoryService()


@history_router.get("/get-result", operation_id="get_result")
def get_result(
    operation_id: str = Query(..., description="Operation ID returned when the scan was initiated"),
):
    """
    Retrieve the full status and result of a scan operation.

    Poll this endpoint after calling any scan tool.  The ``status`` field
    transitions: **PENDING → RUNNING → COMPLETED | FAILED**.

    Once ``status`` is ``COMPLETED`` the ``result`` field contains the full
    :class:`ZapScanResult` payload including all alerts, summary, TLS info,
    and a ``report_url`` link to the HTML report.
    """
    record = _service.get_result(operation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Operation '{operation_id}' not found.",
        )
    return record


@history_router.get("/ids/{batch_type}", operation_id="list_ids_by_type")
def list_ids_by_type(batch_type: str):
    """
    Return the operation IDs for all recorded operations of a given scan type.

    Useful for quickly discovering which operation IDs exist before fetching
    their full results with ``/get-result``.

    **batch_type** is case-insensitive.
    """
    ids = _service.list_ids_by_type(batch_type)
    return {
        "batch_type": batch_type.lower(),
        "operation_ids": ids,
        "count": len(ids),
    }


@history_router.get("/list", operation_id="list_operations")
def list_operations(
    batch_type: Optional[str] = Query(
        None,
        description=(
            "Filter by scan type. Allowed values: "
            f"{', '.join(sorted(BatchType.ALL_SCAN_TYPES))}."
        ),
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by status (PENDING, RUNNING, COMPLETED, FAILED). Comma-separated for multiple.",
    ),
):
    """
    List all recorded operations.

    Optionally filter by **batch_type** or **status**.  When both are
    supplied, **batch_type** takes precedence.

    The ``result`` field is excluded from list responses to keep payloads
    small; use ``/get-result`` for the full record.
    """
    if batch_type:
        return _service.list_by_type(batch_type)

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        return _service.list_by_status(*statuses)

    return _service.list_all()

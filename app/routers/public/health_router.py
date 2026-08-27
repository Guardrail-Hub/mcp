"""
Health endpoints — thin HTTP layer only.

All probing/business logic lives in
:class:`app.services.public.health_service.HealthService`; this router only
maps routes to that service and translates a not-ready state into HTTP 503.
"""

from fastapi import APIRouter, HTTPException

from app.services.public import HealthService

health_router = APIRouter(prefix="/health", tags=["health"])
_service = HealthService()


@health_router.get("")
async def health_check():
    return await live_check()


@health_router.get("/live")
async def live_check():
    return _service.liveness_payload()


@health_router.get("/ready")
async def readiness_check():
    payload = _service.readiness_payload()

    if payload["status"] != "ok":
        raise HTTPException(status_code=503, detail=payload)

    return payload

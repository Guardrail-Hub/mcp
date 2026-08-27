"""Health / readiness probing service.

Owns the infrastructure checks behind the health endpoints — TCP-probing the
configured storage backend and shaping the liveness/readiness payloads — so
that ``routers/public/health_router.py`` stays a thin HTTP adapter, honouring
the ``routers -> services`` dependency rule
(``.ai/standards/architecture/backend-layering``).
"""

import socket
from typing import Optional, Tuple

from urllib.parse import urlparse

from app.core.config import settings

_SERVICE_NAME = "guardrail-mcp-server"


class HealthService:
    """Computes liveness and readiness state, including storage reachability."""

    def liveness_payload(self) -> dict:
        """Static liveness info — the process is up and serving."""
        return {
            "status": "ok",
            "service": _SERVICE_NAME,
            "version": settings.app_version,
            "environment": settings.app_env,
        }

    def readiness_payload(self) -> dict:
        """Readiness info, including a live probe of the configured storage backend."""
        storage = self.storage_status()
        return {
            "status": "ok" if storage["status"] in {"ok", "skipped"} else "error",
            "service": _SERVICE_NAME,
            "storage": storage,
            "audit_mode": settings.audit_mode,
            "token_authority": settings.token_authority,
        }

    # ── Storage probing ──────────────────────────────────────────────────

    def storage_status(self) -> dict:
        """Probe the configured persistence backend and report reachability."""
        provider = settings.database_provider

        if provider == "NONE":
            return {"provider": provider, "status": "skipped"}

        if provider == "POSTGRES":
            host, port = self._postgres_target()
            return {"provider": provider, **self._tcp_check(host, port)}

        if provider == "DYNAMODB":
            target = self._dynamodb_target()
            if target is None:
                return {
                    "provider": provider,
                    "status": "skipped",
                    "mode": "aws",
                    "region": settings.aws_region,
                }

            host, port = target
            return {"provider": provider, **self._tcp_check(host, port)}

        return {"provider": provider, "status": "error", "message": "Unknown provider"}

    @staticmethod
    def _tcp_check(host: str, port: int, timeout_seconds: float = 1.5) -> dict:
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return {"status": "ok", "host": host, "port": port}
        except OSError as exc:
            return {
                "status": "error",
                "host": host,
                "port": port,
                "message": str(exc),
            }

    @staticmethod
    def _postgres_target() -> Tuple[str, int]:
        if settings.database_url:
            parsed = urlparse(settings.database_url)
            if parsed.hostname:
                return parsed.hostname, parsed.port or 5432

        return settings.postgres_host, settings.postgres_port

    @staticmethod
    def _dynamodb_target() -> Optional[Tuple[str, int]]:
        if not settings.dynamodb_endpoint_url:
            return None

        parsed = urlparse(settings.dynamodb_endpoint_url)
        if not parsed.hostname:
            return None

        default_port = 443 if parsed.scheme == "https" else 80
        return parsed.hostname, parsed.port or default_port

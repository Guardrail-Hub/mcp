"""
Main entry point for the GuardRail MCP Server.
"""

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import FastApiMCP

from app.constants.paths import ReportPaths
from app.core.config import settings
from app.core.logging_config import configure_access_logging
from app.routers.endpoints import MCP_OPERATIONS, tools_router
from app.routers.public.health_router import health_router
from app.routers.public.jmeter_worker_router import jmeter_worker_router
from app.routers.public.worker_directory_router import worker_directory_router
from app.routers.public.zap_worker_router import (
    zap_worker_legacy_router,
    zap_worker_router,
)

# Suppress the high-frequency worker-heartbeat access log while keeping access
# logging enabled for every other endpoint. Done at import time so the filter
# is in place before uvicorn serves the first request.
configure_access_logging()

# ---------------------------------------------------------------------------
# Platform lifecycle notifications (operational visibility).
# Fully defensive: all reporting is lazily imported and guarded so it can never
# crash, block, or delay platform startup/shutdown beyond its own work.
# ---------------------------------------------------------------------------
_DB_DISPLAY = {"NONE": "None", "POSTGRES": "PostgreSQL", "DYNAMODB": "DynamoDB"}


def _install_fatal_error_hook(reporter) -> None:
    """Report a fatal error on any unhandled exception, then defer to the default hook."""
    original = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            reporter.stopped(f"{exc_type.__name__}: {exc}")
        except Exception:  # pragma: no cover - must not mask the original crash
            pass
        original(exc_type, exc, tb)

    sys.excepthook = hook


@asynccontextmanager
async def lifespan(_app):
    # Steps 1-4 of the startup sequence (Database Ready -> Run Migrations ->
    # Verify required tables -> Initialize services) run FIRST and
    # deliberately OUTSIDE the defensive try/except below: a schema failure
    # here must propagate out of this function so uvicorn aborts startup with
    # a non-zero exit code rather than serving requests against a bad schema.
    # Only after this succeeds do steps 5-6 (start server, accept requests)
    # happen — which, for uvicorn, is exactly "this lifespan startup returns".
    from app.core.startup import run_startup_sequence

    startup = run_startup_sequence(settings)

    reporter = None
    try:
        from app.bootstrap import build_platform_reporter
        from app.services.platform.lifecycle import report_startup

        reporter = build_platform_reporter()
        _install_fatal_error_hook(reporter)

        slack_connection = None
        slack_channel = None
        if settings.slack_enabled:
            from app.integrations.chat.slack.connection import (
                check_connection,
                resolve_channel_name,
            )

            slack_connection = check_connection(settings.slack_bot_token)
            slack_channel = resolve_channel_name(
                settings.slack_bot_token, settings.slack_default_channel
            )

        report_startup(
            reporter,
            environment=settings.app_env,
            version=settings.app_version,
            database_provider=_DB_DISPLAY.get(settings.database_provider, settings.database_provider),
            notification_provider="Slack" if settings.slack_enabled else "None",
            log_level=settings.log_level,
            tools=list(MCP_OPERATIONS),
            slack_connection=slack_connection,
            slack_channel=slack_channel,
            startup_time=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:  # noqa: BLE001 - startup reporting must never crash boot
        try:
            if reporter is not None:
                reporter.stopped(f"startup reporting failed: {exc}")
        except Exception:
            pass

    yield

    if startup is not None:
        # 1) Stop assigning new work so no operation starts mid-teardown.
        #    Both engines' dispatchers, each stopped independently so one
        #    failing to stop cannot leave the other running.
        for _dispatcher in (startup.dispatcher, startup.jmeter_dispatcher):
            try:
                if _dispatcher is not None:
                    _dispatcher.stop()
            except Exception:  # noqa: BLE001 - shutdown must never crash teardown
                pass

        # 2) Fail any operation still RUNNING at shutdown so it never hangs in a
        #    non-terminal state; the user is notified via the existing pipeline
        #    (still live) before the ticker is stopped below.
        try:
            from app.core.startup import (
                SHUTDOWN_FAILURE_REASON,
                recover_interrupted_operations,
            )
            from app.integrations.jmeter.runtime import registry as jmeter_registry
            from app.integrations.owasp_zap.runtime import registry

            recover_interrupted_operations(
                startup.operation_service,
                startup.dao,
                SHUTDOWN_FAILURE_REASON,
                registries=(registry, jmeter_registry),
            )
        except Exception:  # noqa: BLE001 - shutdown recovery must never crash teardown
            pass

        # 3) Stop the elapsed-time refresh ticker.
        try:
            if startup.notifier is not None:
                startup.notifier.stop()
        except Exception:  # noqa: BLE001 - shutdown must never crash teardown
            pass

    try:
        if reporter is not None:
            from app.services.platform.lifecycle import report_shutdown

            report_shutdown(reporter)
    except Exception:  # noqa: BLE001 - shutdown reporting must never crash teardown
        pass


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.app_debug,
    lifespan=lifespan,
)

# FastAPI Middleware
if settings.cors_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Routers
app.include_router(health_router)
# The cross-engine view owns GET /workers and is registered before the legacy
# ZAP prefix so the intent is visible in the file, not just in path matching.
app.include_router(worker_directory_router)
app.include_router(zap_worker_router)
app.include_router(zap_worker_legacy_router)
app.include_router(jmeter_worker_router)
app.include_router(tools_router)

# Static files — generic public asset server (reports, future tools UI, etc.)
_public_dir = Path(ReportPaths.BASE_DIR)
_public_dir.mkdir(parents=True, exist_ok=True)
app.mount("/public", StaticFiles(directory=str(_public_dir), html=True), name="public")

# MCP — only expose tool endpoints (not health/root) as MCP tools
mcp = FastApiMCP(
    app,
    name="GuardRail MCP Server",
    description=(
        "Security testing tools powered by OWASP ZAP. "
        "Scan APIs and websites for vulnerabilities, run authenticated spider scans, "
        "and perform interactive browser-based security assessments."
    ),
    include_operations=MCP_OPERATIONS,
)

mcp.mount_http(mount_path="/mcp")
mcp.setup_server()


@app.get("/")
async def root():
    from app.integrations.owasp_zap.runtime import registry as zap_worker_registry

    workers = zap_worker_registry.list_all()
    return {
        "name": "guardrail-mcp-server",
        "mode": settings.mcp_mode,
        "token_authority": settings.token_authority,
        "database_provider": settings.database_provider,
        "zap_workers_registered": len(workers),
        "zap_workers_idle": sum(1 for w in workers if w.is_schedulable),
        "status": "running",
        "health": "/health",
        "workers": "/workers",
        "mcp": "/mcp",
        "tools": MCP_OPERATIONS,
    }

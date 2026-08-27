"""
Application startup sequence.

Required order (never violated):

    1. Database Ready
    2. Run Migrations
    3. Verify required tables
    4. Initialize services
    5. Start HTTP Server
    6. Accept Requests

Steps 1-4 run here, inside the ASGI lifespan's startup phase, *before*
``yield`` in ``app/main.py``. Uvicorn does not start accepting connections
until lifespan startup completes, and aborts the whole process (non-zero
exit) if that phase raises — so "stop startup on migration failure, exit
non-zero" falls directly out of raising here rather than being a convention
someone has to remember to check.
"""

from dataclasses import dataclass
from typing import Optional

from app.core.mcp_logger import MCPLogger
from app.domain.lifecycle import OperationPhase

# Reasons attached to operations force-failed by lifecycle recovery. Kept as
# plain strings (no new status/category) to preserve the simple lifecycle model.
RESTART_FAILURE_REASON = "Server restarted"
SHUTDOWN_FAILURE_REASON = "Server shutdown for maintenance"


class StartupError(RuntimeError):
    """Raised when the startup sequence cannot complete; must abort boot."""


@dataclass
class StartupResult:
    """Handles the app lifespan needs after startup.

    ``dispatcher`` is the running ZAP queue dispatcher and ``jmeter_dispatcher``
    the JMeter one — one per engine, since each owns its own queue lane and
    worker pool; both are stopped on shutdown. ``notifier`` (optional) owns the
    elapsed-time refresh ticker; and ``operation_service``/``dao`` let the
    shutdown path fail any operation still RUNNING at teardown. All are
    process-scoped singletons assembled once here.
    """

    dispatcher: object
    operation_service: object
    dao: object
    notifier: object = None
    jmeter_dispatcher: object = None


def recover_interrupted_operations(
    operation_service,
    dao,
    reason: str,
    logger=None,
    *,
    registries=(),
) -> int:
    """Force every still-RUNNING operation to FAILED with *reason*.

    An operation is only RUNNING while a worker actively drives it; if the
    process died or is shutting down, that in-memory execution is gone and the
    row can never resume, so it would otherwise stay RUNNING forever. This marks
    each one FAILED through the normal ``OperationService`` path — so the
    lifecycle event fires and the user is notified — and best-effort releases any
    worker still bound to it (returning that worker to IDLE). Never raises: a
    recovery failure must not block startup or shutdown.

    Args:
        registries: Every engine's worker registry (ZAP's, JMeter's). Recovery
            spans all tools because the RUNNING rows do, so each registry gets
            the same best-effort release pass. Empty means "release nothing".

    Returns the number of operations recovered.
    """
    logger = logger or MCPLogger("startup")
    try:
        running = dao.get_by_status(OperationPhase.RUNNING)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Recovery: could not query RUNNING operations: %s", exc)
        return 0

    recovered_ids: list[str] = []
    for record in running:
        try:
            # FAILED transition + lifecycle event + user notification, all via
            # the single OperationService owner (no new status introduced).
            operation_service.fail(record.operation_id, reason)
            recovered_ids.append(record.operation_id)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(
                "Recovery: could not fail operation '%s': %s",
                record.operation_id,
                exc,
            )

    # Clear any worker ownership so a bound worker returns to IDLE. Normally a
    # no-op at startup (the in-memory registry is rebuilt empty on restart);
    # meaningful at graceful shutdown, where workers may still hold an op_id.
    if recovered_ids:
        for registry in registries:
            try:
                for worker in registry.list_all():
                    if worker.op_id in recovered_ids:
                        registry.release(worker.worker_id, worker.op_id)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Recovery: could not release worker ownership: %s", exc)

    if recovered_ids:
        logger.warning(
            "Recovered %d interrupted operation(s) as FAILED (%s)",
            len(recovered_ids),
            reason,
        )
    return len(recovered_ids)


def run_startup_sequence(settings) -> Optional[StartupResult]:
    """Run steps 1-4 of the startup sequence and start the ZAP queue dispatcher.

    Returns a :class:`StartupResult` (dispatcher + notifier + operation service +
    dao, so the lifespan can manage shutdown), or ``None`` when
    ``DATABASE_PROVIDER=NONE`` — a supported "no persistence" deployment mode in
    which the ZAP queue simply has nothing to poll, but every other endpoint
    (health, etc.) still works.

    Raises:
        StartupError: If the database schema cannot be verified/migrated.
            Must be allowed to propagate out of the ASGI lifespan so the
            server aborts with a non-zero exit code — never start the API
            with an invalid database schema.
    """
    logger = MCPLogger("startup")

    if settings.database_provider == "NONE":
        logger.warning(
            "DATABASE_PROVIDER=NONE — operations are not persisted, so the ZAP "
            "queue dispatcher will not start. Endpoints that don't require "
            "persistence (health, etc.) are unaffected."
        )
        return None

    # 1. Database Ready / 2. Run Migrations / 3. Verify required tables
    from app.dao.operation_dao import get_operation_dao  # noqa: PLC0415

    try:
        dao = get_operation_dao()
        dao.initialize_schema()
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Startup aborted — database schema not ready: %s", exc)
        raise StartupError(f"Database schema initialization failed: {exc}") from exc

    # 4. Initialize services. Assemble EXACTLY ONE notification pipeline (one
    #    event bus, one OperationService, one OperationNotifier subscribed only
    #    when a transport is enabled), then hand its shared OperationService to
    #    the ZAP dispatcher + scanner so their lifecycle/progress events publish
    #    onto that single bus. No scan component knows any transport, and there
    #    is no second subscription.
    from app.bootstrap import (  # noqa: PLC0415
        build_jmeter_dispatcher,
        build_operation_notification_flow_from_settings,
        build_zap_dispatcher,
    )

    flow = build_operation_notification_flow_from_settings(dao=dao, config=settings)

    # Recovery — BEFORE the dispatcher pulls any new work. Operations left
    # RUNNING by a previous crash/restart can never resume (their in-memory
    # execution is gone), so fail them now: FAILED + lifecycle event + user
    # notification, and release any worker still bound to them.
    from app.integrations.jmeter.runtime import registry as jmeter_registry  # noqa: PLC0415
    from app.integrations.owasp_zap.runtime import registry  # noqa: PLC0415

    recover_interrupted_operations(
        flow.operation_service,
        dao,
        RESTART_FAILURE_REASON,
        logger,
        registries=(registry, jmeter_registry),
    )

    dispatcher = build_zap_dispatcher(
        dao=dao, operation_service=flow.operation_service, config=settings
    )
    dispatcher.start()

    # One dispatcher per engine: each polls only its own queue lane, so they
    # never see each other's operations (ADR-0011).
    jmeter_dispatcher = build_jmeter_dispatcher(
        dao=dao, operation_service=flow.operation_service, config=settings
    )
    jmeter_dispatcher.start()

    # Keep RUNNING notifications' elapsed clock advancing between progress
    # events (edits the existing message only). Started here, stopped by the
    # lifespan on shutdown. No-op when no update-capable channel is enabled.
    if flow.notifier is not None:
        flow.notifier.start()

    logger.info(
        "Startup sequence complete — database ready, notification pipeline wired "
        "(%d channel(s)), ZAP and JMeter queue dispatchers running",
        len(flow.notification_service.channels),
    )
    return StartupResult(
        dispatcher=dispatcher,
        operation_service=flow.operation_service,
        dao=dao,
        notifier=flow.notifier,
        jmeter_dispatcher=jmeter_dispatcher,
    )

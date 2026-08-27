"""In-process composition root for the Operation -> Notification flow.

Assembles the working pipeline entirely in-process (no HTTP, no background
workers):

    OperationService
        -> EventPublisher (InProcessEventDispatcher)
        -> EventNotificationSubscriber -> NotificationService
        -> SlackNotificationChannel

This is application assembly, kept separate from ``main.py`` because the flow is
in-process and has no HTTP surface. Dependencies are injected so the flow is
fully testable; a production entry point can call :func:`build_notification_flow`
with settings and the real DAO when one exists.
"""

from dataclasses import dataclass
from typing import Optional

from app.constants.batch import BatchType
from app.core.events.dispatcher import InProcessEventDispatcher
from app.dao.base import BaseOperationDAO
from app.integrations.chat.slack.channel_adapter import (
    SlackNotificationChannel,
    Transport,
)
from app.services.notification.subscribers.event_subscriber import EventNotificationSubscriber
from app.services.notification.notification_service import NotificationService
from app.services.notification.dispatch.operation_notifier import OperationNotifier
from app.domain.lifecycle import OperationPhase
from app.services.operations.operation_service import (
    EVENT_OPERATION_CANCELLED,
    EVENT_OPERATION_COMPLETED,
    EVENT_OPERATION_CREATED,
    EVENT_OPERATION_FAILED,
    EVENT_OPERATION_PROGRESS,
    EVENT_OPERATION_STARTED,
    OperationService,
)

SLACK_CHANNEL_NAME = "slack"


def _format_scan_target(metadata) -> Optional[str]:
    """Build a user-facing scan target (e.g. ``POST /api/auth/login``) from a
    persisted operation's metadata. Presentation-only and defensive: returns
    ``None`` when nothing usable is present so the card omits the line."""
    from urllib.parse import urlparse  # noqa: PLC0415

    if not isinstance(metadata, dict):
        return None
    url = metadata.get("target_url") or metadata.get("url") or metadata.get("target")
    if not url:
        request = metadata.get("request")
        if isinstance(request, dict):
            url = request.get("url")
    if not url:
        return None

    method = metadata.get("method")
    method = str(method) if isinstance(method, (str, int)) else None
    try:
        parsed = urlparse(str(url))
        path = parsed.path or ""
    except Exception:  # noqa: BLE001 - malformed url → fall back to raw value
        parsed, path = None, ""
    if method and parsed and parsed.scheme and path not in ("", "/"):
        return f"{method} {path}"
    return str(url)


def _operation_context_resolver(dao: BaseOperationDAO):
    """Return a read-only resolver ``operation_id -> {target, title}`` for the
    notifier. Read-only and best-effort — it never mutates state or raises."""

    def resolve(operation_id: str):
        try:
            record = dao.get_operation(operation_id)
        except Exception:  # noqa: BLE001 - context is optional
            return None
        if record is None:
            return None
        # A completed operation's persisted result carries the report link; it is
        # absent earlier in the lifecycle (the resolver simply returns None then).
        result = record.result if isinstance(record.result, dict) else {}
        # ZAP files its links under "reports", JMeter under "artifacts" (each is
        # that tool's own wire format — ADR-0010). ZAP's key is read first and is
        # unchanged; without the second, every JMeter notification rendered with
        # no report link at all, which is required by result-contract.md §5.
        links = result.get("reports") or result.get("artifacts") or {}
        links = links if isinstance(links, dict) else {}
        return {
            "target": _format_scan_target(record.metadata),
            "title": OPERATION_TITLES.get(record.batch_type),
            "report_link": links.get("view_report"),
        }

    return resolve

# Which operation events notify Slack, and the message each renders.
OPERATION_NOTIFICATION_TEMPLATES = {
    EVENT_OPERATION_CREATED: "Operation {operation_id} created.",
    EVENT_OPERATION_COMPLETED: "Operation {operation_id} completed.",
}

# Maps the operations domain's lifecycle events onto the notification layer's
# vocabulary-neutral phases. Defined here (the composition root) so the notifier
# stays decoupled from the operations domain.
OPERATION_LIFECYCLE_PHASES = {
    EVENT_OPERATION_CREATED: OperationPhase.QUEUED,
    EVENT_OPERATION_STARTED: OperationPhase.RUNNING,
    EVENT_OPERATION_COMPLETED: OperationPhase.COMPLETED,
    EVENT_OPERATION_FAILED: OperationPhase.FAILED,
    EVENT_OPERATION_CANCELLED: OperationPhase.CANCELLED,
}

# Events treated as throttled, intermediate progress rather than transitions.
OPERATION_PROGRESS_EVENTS = frozenset({EVENT_OPERATION_PROGRESS})

# Presentation: operation batch_type -> friendly, user-facing title. Defined
# here (the composition root) so the notifier stays tool-agnostic; a new tool
# just adds a row. Unmapped types fall back to the renderer's default title.
OPERATION_TITLES = {
    BatchType.API_SCAN: "API Security Scan",
    BatchType.WEB_SCAN: "Website Security Scan",
    BatchType.INTERACTIVE_SCAN: "Interactive Website Scan",
    BatchType.BATCH_SCAN: "Scenario Security Scan",
    BatchType.SUITE_SCAN: "Application Security Scan",
    BatchType.JMETER_TEST: "JMeter Load Test",
}


@dataclass
class NotificationFlow:
    """The assembled in-process flow. ``operation_service`` is the entry point."""

    operation_service: OperationService
    notification_service: NotificationService
    dispatcher: InProcessEventDispatcher
    # The capability-aware notifier, when one was wired (i.e. a transport is
    # enabled). Exposed so the app lifespan can start/stop its elapsed-time
    # refresh ticker. ``None`` for the template-only flows and when no channel
    # is enabled.
    notifier: Optional[OperationNotifier] = None


def build_notification_flow(
    *,
    dao: BaseOperationDAO,
    slack_token: str,
    destination: str,
    slack_transport: Optional[Transport] = None,
) -> NotificationFlow:
    """Wire the Operation -> Notification -> Slack flow and return it.

    Args:
        dao: Persistence backend for operations (existing ``BaseOperationDAO``).
        slack_token: Slack bot token for the Slack channel adapter.
        destination: Slack channel id notifications are delivered to.
        slack_transport: Optional HTTP transport for the Slack adapter; inject a
            fake in tests to avoid the network.

    Returns:
        The assembled :class:`NotificationFlow`.
    """
    dispatcher = InProcessEventDispatcher()

    slack = SlackNotificationChannel(slack_token, transport=slack_transport)
    notification_service = NotificationService([slack])

    subscriber = EventNotificationSubscriber(
        notification_service,
        channel=SLACK_CHANNEL_NAME,
        destination=destination,
        templates=OPERATION_NOTIFICATION_TEMPLATES,
    )
    for event_name in subscriber.event_names:
        dispatcher.subscribe(event_name, subscriber.handle)

    operation_service = OperationService(dao, dispatcher)
    return NotificationFlow(
        operation_service=operation_service,
        notification_service=notification_service,
        dispatcher=dispatcher,
    )


def build_platform_reporter(config=None, *, logger=None, slack_transport=None):
    """Build a PlatformReporter from settings.

    Reuses the existing NotificationService + SlackNotificationChannel + logger.
    When Slack is disabled, the reporter logs only (notification_service=None);
    lifecycle events are still fully logged.

    Args:
        config: Settings object; defaults to ``app.core.config.settings`` (lazy).
        logger: Logger to use; defaults to ``MCPLogger("platform")``.
        slack_transport: Optional Slack HTTP transport (tests).

    Returns:
        A ready ``PlatformReporter``.
    """
    if config is None:
        from app.core.config import settings as config
    if logger is None:
        from app.core.mcp_logger import MCPLogger

        logger = MCPLogger("platform")

    from app.services.platform.platform_reporter import PlatformReporter

    notification_service = None
    destination = None
    if config.slack_enabled:
        from app.integrations.chat.slack.channel_adapter import SlackNotificationChannel
        from app.services.notification.notification_service import NotificationService

        slack = SlackNotificationChannel(config.slack_bot_token, transport=slack_transport)
        notification_service = NotificationService([slack])
        destination = config.slack_default_channel

    return PlatformReporter(
        logger, notification_service=notification_service, destination=destination
    )


def build_zap_evaluation_service(operation_service, *, scan_runner=None):
    """Wire the OWASP ZAP evaluation flow onto an existing OperationService.

    Reuses the given ``OperationService`` (and therefore its dispatcher /
    notification pipeline). The scan runner defaults to the synchronous
    ``ZapScanRunner`` over the existing ZapClient; inject a fake in tests.

    Args:
        operation_service: The lifecycle service the evaluation drives.
        scan_runner: Optional synchronous scan callable. Defaults to
            ``ZapScanRunner()`` (imported lazily so this module does not require
            the zaproxy SDK).

    Returns:
        A ready ``EvaluationService``.
    """
    from app.services.evaluations.evaluation_service import EvaluationService

    if scan_runner is None:
        from app.services.tools.owasp_zap.sync_runner import ZapScanRunner

        scan_runner = ZapScanRunner()
    return EvaluationService(operation_service, scan_runner)


def build_notification_flow_from_settings(
    *,
    dao: BaseOperationDAO,
    config=None,
    slack_transport: Optional[Transport] = None,
) -> NotificationFlow:
    """Assemble the flow from the Settings layer (production wiring).

    The Slack channel is registered and operation events are subscribed **only
    when ``SLACK_ENABLED`` is true**. When disabled, operations still run and
    publish events, but no channel is registered and nothing subscribes, so no
    notifications are delivered.

    Args:
        dao: Persistence backend (existing ``BaseOperationDAO``).
        config: Settings object to read; defaults to ``app.core.config.settings``.
            Imported lazily so importing this module does not require loading
            settings (and injectable in tests).
        slack_transport: Optional HTTP transport for the Slack adapter (tests).

    Returns:
        The assembled :class:`NotificationFlow`.
    """
    if config is None:
        from app.core.config import settings as config

    dispatcher = InProcessEventDispatcher()

    channels = []
    if config.slack_enabled:
        channels.append(
            SlackNotificationChannel(config.slack_bot_token, transport=slack_transport)
        )
    notification_service = NotificationService(channels)

    if config.slack_enabled:
        subscriber = EventNotificationSubscriber(
            notification_service,
            channel=SLACK_CHANNEL_NAME,
            destination=config.slack_default_channel,
            templates=OPERATION_NOTIFICATION_TEMPLATES,
        )
        for event_name in subscriber.event_names:
            dispatcher.subscribe(event_name, subscriber.handle)

    operation_service = OperationService(dao, dispatcher)
    return NotificationFlow(
        operation_service=operation_service,
        notification_service=notification_service,
        dispatcher=dispatcher,
    )


def build_zap_operation_registry(*, operation_service):
    """Assemble the ZAP operation registry (the generic execution routing table).

    This is the single place where ZAP tools are registered. **Adding a new tool
    is one line here and zero lines in the Dispatcher or WorkerService** — the
    whole point of the generic execution layer (see
    ``architecture/zap-generic-execution/DESIGN.md``).

    Args:
        operation_service: The shared lifecycle service, threaded into each
            tool's handler so lifecycle + progress events publish onto the one
            event bus the notifier subscribes to.

    Returns:
        A populated ``ZapOperationRegistry``.
    """
    from app.services.tools.owasp_zap.execution.registry import (  # noqa: PLC0415
        ZapOperationRegistry,
    )
    from app.services.tools.owasp_zap.execution.handlers import (  # noqa: PLC0415
        build_api_scan_operation,
        build_api_scenario_operation,
        build_api_suite_operation,
    )

    registry = ZapOperationRegistry()
    registry.register(build_api_scan_operation(operation_service))
    registry.register(build_api_scenario_operation(operation_service))
    registry.register(build_api_suite_operation(operation_service))
    # Future tools plug in the same way (browser-automation architecture still
    # under evaluation — intentionally not implemented):
    #   registry.register(build_web_application_operation(operation_service))
    #   registry.register(build_interactive_session_operation(operation_service))
    return registry


def build_zap_dispatcher(*, dao: BaseOperationDAO, operation_service, config=None):
    """Assemble the ZAP queue dispatcher (background worker-pool dispatch).

    Wires the process-wide Pool Manager singletons
    (``app.integrations.owasp_zap.runtime``) to *dao* and the generic
    ``ZapOperationRegistry`` (via :func:`build_zap_operation_registry`). The
    Dispatcher no longer depends on ``ZapApiScanService`` — it resolves handlers
    through the registry. The returned dispatcher is not started — the caller
    (``app/core/startup.py``) is responsible for ``.start()``/``.stop()`` as part
    of the app lifespan, matching this module's "assemble here, run there" split.

    Args:
        dao: The persistence backend for operations — required, since a
            dispatcher with nowhere to read the queue from cannot do
            anything. Callers should only invoke this when
            ``DATABASE_PROVIDER`` is not ``NONE``.
        operation_service: The shared lifecycle service (from
            :func:`build_operation_notification_flow_from_settings`). Both the
            dispatcher's FAILED transitions and each tool's lifecycle + progress
            events are published through it onto the one event bus the notifier
            subscribes to — so no scan component knows any transport.
        config: Settings object; defaults to ``app.core.config.settings``.

    Returns:
        A ready, unstarted ``OwaspZapDispatcher``.
    """
    if config is None:
        from app.core.config import settings as config  # noqa: PLC0415

    from app.integrations.owasp_zap.runtime import pool_manager  # noqa: PLC0415
    from app.services.tools.owasp_zap.execution.dispatcher import OwaspZapDispatcher  # noqa: PLC0415

    registry = build_zap_operation_registry(operation_service=operation_service)

    return OwaspZapDispatcher(
        operation_dao=dao,
        operation_service=operation_service,
        pool_manager=pool_manager,
        registry=registry,
        queue_poll_interval_seconds=config.zap_queue_poll_interval_seconds,
        heartbeat_sweep_interval_seconds=config.zap_worker_heartbeat_sweep_interval_seconds,
        heartbeat_timeout_seconds=config.zap_worker_heartbeat_timeout_seconds,
    )


def build_jmeter_dispatcher(*, dao: BaseOperationDAO, operation_service, config=None):
    """Assemble the JMeter queue dispatcher (background worker-pool dispatch).

    Wires the process-wide JMeter pool singletons
    (``app.integrations.jmeter.runtime``) to *dao* and a ``JMeterRuntime``. The
    returned dispatcher is not started — ``app/core/startup.py`` owns
    ``.start()``/``.stop()`` as part of the app lifespan, matching this module's
    "assemble here, run there" split.

    Separate from :func:`build_zap_dispatcher` on purpose: the two engines have
    their own pools, their own queue lanes and their own intervals, and share
    only the Operations table and the lifecycle service (ADR-0011).

    Args:
        dao: The persistence backend for operations — required, since a
            dispatcher with nowhere to read the queue from cannot do anything.
        operation_service: The shared lifecycle service, so this dispatcher's
            FAILED transitions publish onto the same event bus the notifier
            subscribes to.
        config: Settings object; defaults to ``app.core.config.settings``.

    Returns:
        A ready, unstarted ``JMeterDispatcher``.
    """
    if config is None:
        from app.core.config import settings as config  # noqa: PLC0415

    from app.integrations.jmeter.runtime import pool_manager  # noqa: PLC0415
    from app.services.tools.jmeter.execution.dispatcher import (  # noqa: PLC0415
        JMeterDispatcher,
    )
    from app.services.tools.jmeter.execution.runtime import JMeterRuntime  # noqa: PLC0415

    return JMeterDispatcher(
        operation_dao=dao,
        operation_service=operation_service,
        pool_manager=pool_manager,
        runtime=JMeterRuntime(operation_service),
        queue_poll_interval_seconds=config.jmeter_queue_poll_interval_seconds,
        heartbeat_sweep_interval_seconds=(
            config.jmeter_worker_heartbeat_sweep_interval_seconds
        ),
        heartbeat_timeout_seconds=config.jmeter_worker_heartbeat_timeout_seconds,
    )


def build_operation_notification_flow(
    *,
    dao: BaseOperationDAO,
    channels: Optional[list] = None,
    slack_token: Optional[str] = None,
    destination: str,
    channel_name: str = SLACK_CHANNEL_NAME,
    debug: bool = False,
    slack_transport: Optional[Transport] = None,
) -> NotificationFlow:
    """Wire the capability-aware Operation -> Notification flow and return it.

    Unlike :func:`build_notification_flow` (a simple template-per-event path),
    this assembles the full progress framework: the :class:`OperationNotifier`
    reads the target channel's capabilities and picks the message-update or
    new-message-fallback strategy, throttles progress, and renders each lifecycle
    phase per the UX requirements. Adding another platform is just passing a
    different channel adapter here — no change to any of the wired units.

    Args:
        dao: Persistence backend for operations (existing ``BaseOperationDAO``).
        channels: Pre-built notification channels to register. When ``None``, a
            :class:`SlackNotificationChannel` is built from *slack_token*.
        slack_token: Slack bot token, used only when *channels* is ``None``.
        destination: Channel-native destination notifications are delivered to.
        channel_name: Which registered channel the notifier targets (defaults to
            Slack). Lets a caller target any adapter it registered.
        slack_transport: Optional Slack HTTP transport (tests).

    Returns:
        The assembled :class:`NotificationFlow`.
    """
    dispatcher = InProcessEventDispatcher()

    if channels is None:
        channels = [SlackNotificationChannel(slack_token, transport=slack_transport)]
    notification_service = NotificationService(channels)

    notifier = OperationNotifier(
        notification_service,
        channel=channel_name,
        destination=destination,
        lifecycle_events=OPERATION_LIFECYCLE_PHASES,
        progress_events=OPERATION_PROGRESS_EVENTS,
        titles=OPERATION_TITLES,
        debug=debug,
        context_resolver=_operation_context_resolver(dao),
    )
    for event_name in notifier.event_names:
        dispatcher.subscribe(event_name, notifier.handle)

    operation_service = OperationService(dao, dispatcher)
    return NotificationFlow(
        operation_service=operation_service,
        notification_service=notification_service,
        dispatcher=dispatcher,
        notifier=notifier,
    )


def build_operation_notification_flow_from_settings(
    *,
    dao: BaseOperationDAO,
    config=None,
    slack_transport: Optional[Transport] = None,
) -> NotificationFlow:
    """Assemble the capability-aware Operation -> Notification flow from Settings.

    This is the ONE production pipeline: a single event bus, a single
    ``OperationService`` publishing onto it, and a single ``OperationNotifier``
    subscribed to it — but only when a transport is enabled. When
    ``SLACK_ENABLED`` is false, no channel is registered and nothing subscribes,
    so operations still run and publish events (harmlessly, to no subscriber) and
    no notifications are delivered. Callers share the returned
    ``operation_service`` with the scan path so every tool's lifecycle + progress
    events reach the one notifier (future tools need no new wiring here).

    Args:
        dao: Persistence backend (existing ``BaseOperationDAO``).
        config: Settings object; defaults to ``app.core.config.settings`` (lazy).
        slack_transport: Optional Slack HTTP transport (tests).

    Returns:
        The assembled :class:`NotificationFlow` (its ``operation_service`` is the
        shared entry point handed to the scan dispatcher).
    """
    if config is None:
        from app.core.config import settings as config  # noqa: PLC0415

    dispatcher = InProcessEventDispatcher()

    channels = []
    if config.slack_enabled:
        channels.append(
            SlackNotificationChannel(config.slack_bot_token, transport=slack_transport)
        )
    notification_service = NotificationService(channels)

    notifier = None
    if config.slack_enabled:
        notifier = OperationNotifier(
            notification_service,
            channel=SLACK_CHANNEL_NAME,
            destination=config.slack_default_channel,
            lifecycle_events=OPERATION_LIFECYCLE_PHASES,
            progress_events=OPERATION_PROGRESS_EVENTS,
            titles=OPERATION_TITLES,
            debug=getattr(config, "notification_debug", False),
            refresh_interval_seconds=getattr(
                config, "notification_elapsed_refresh_seconds", 15.0
            ),
            context_resolver=_operation_context_resolver(dao),
        )
        for event_name in notifier.event_names:
            dispatcher.subscribe(event_name, notifier.handle)

    operation_service = OperationService(dao, dispatcher)
    return NotificationFlow(
        operation_service=operation_service,
        notification_service=notification_service,
        dispatcher=dispatcher,
        notifier=notifier,
    )
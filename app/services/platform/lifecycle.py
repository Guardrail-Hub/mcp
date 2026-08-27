"""Platform lifecycle orchestration.

Pure sequencing over a :class:`PlatformReporter`. Models the platform as
lifecycle transitions rather than runtime-specific events:

    starting -> ready -> (slack connected)          [on start]
    shutting down                                    [on stop]

There is intentionally no "restart" event. A restart (Docker, systemd,
Kubernetes, …) is just the previous process's *shutting down* followed by the
next process's *starting -> ready*, so the behaviour is runtime-agnostic.

Holds no I/O of its own (reporter + connection info are injected), so it is fully
unit-testable with a spy reporter.
"""

from typing import Any, Iterable, Optional

_SERVER = "mcp-server"


def report_startup(
    reporter: Any,
    *,
    environment: str,
    version: str,
    database_provider: str,
    notification_provider: str,
    log_level: str,
    tools: Iterable[str],
    slack_connection: Optional[Any] = None,
    slack_channel: Optional[str] = None,
    server: str = _SERVER,
    build: Optional[str] = None,
    startup_time: Optional[str] = None,
) -> None:
    """Fire the startup lifecycle as ONE aggregated bot summary.

    The administrator sees a single ✅ Platform Ready message that folds in the
    integration status (e.g. Slack) and overall health — instead of separate
    Starting / Ready / Slack Connected messages. The init details (config loaded,
    tools) remain *log-only*, keeping the logs detailed and the bot concise.
    """
    reporter.configuration_loaded(
        environment=environment,
        database_provider=database_provider,
        notification_provider=notification_provider,
        log_level=log_level,
    )
    reporter.mcp_tools_registered(tools)

    integrations = None
    status = "Healthy"
    if slack_connection is not None:
        if slack_connection.ok:
            channel = slack_channel or ""
            value = f"Connected ({channel})" if channel else "Connected"
            integrations = [("🤖 Slack", value)]
        else:
            integrations = [("🤖 Slack", "Disconnected")]
            status = "Degraded"

    reporter.ready(environment, version, server=server, integrations=integrations, status=status)

    reporter.platform_summary(
        version=version,
        environment=environment,
        database_provider=database_provider,
        notification_provider=notification_provider,
        tools=list(tools),
        build=build,
        startup_time=startup_time,
    )


def report_shutdown(reporter: Any, reason: str = "Manual shutdown") -> None:
    """Fire the shutdown lifecycle event."""
    reporter.shutting_down(reason)

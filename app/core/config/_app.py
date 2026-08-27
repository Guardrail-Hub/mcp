from typing import Literal


class AppMixin:
    # =====================================================
    # App / Server
    # =====================================================
    app_name: str = "GuardRail MCP Server"
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    app_version: str = "0.1.0"

    # Presentation: when true, notification cards include Level-4
    # infrastructure detail (operation id, worker, container, ...). Off by
    # default so the standard user experience stays free of infrastructure.
    notification_debug: bool = False

    # How often (seconds) a RUNNING operation's notification is re-rendered so
    # its elapsed-time keeps advancing even between progress events. The
    # existing message is edited in place (never a new message). Set to 0 to
    # disable the periodic refresh. Kept modest to avoid excessive Slack calls.
    notification_elapsed_refresh_seconds: float = 15.0

    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8787
    mcp_server_public_url: str = "http://localhost:8787"

    # =====================================================
    # Deployment Mode
    # =====================================================
    mcp_mode: Literal["STANDALONE", "CONNECTED"] = "STANDALONE"
    mcp_deployment_mode: Literal["HOSTED_PUBLIC", "SELF_HOSTED_PRIVATE"] = (
        "SELF_HOSTED_PRIVATE"
    )

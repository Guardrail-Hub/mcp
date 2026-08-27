"""Strongly-typed identity of a ZAP tool.

``ZapOperationType`` is the registry key and the value persisted in an
operation's ``metadata["operation_type"]``. It is deliberately a strongly-typed
``str`` enum rather than a bare string so that:

* the registry resolves handlers by a closed, validated set of members;
* an unknown/typo'd type fails fast at ``ZapOperationType(value)`` rather than
  silently routing nowhere;
* the persisted value is stable and human-readable in the Operations table.

This module has no dependencies on any concrete tool or on the handler layer, so
both the tool services (at submit time) and the execution layer (at dispatch
time) can import it without creating a cycle.
"""

from enum import Enum


class ZapOperationType(str, Enum):
    """What is being executed. The registry key; never a raw string."""

    # Implemented.
    API_SCAN = "api_scan"

    # Phase 2 — foundations designed, implemented after the generic layer review.
    API_SCENARIO = "api_scenario"
    API_SUITE = "api_suite"

    # Placeholders only — browser-automation architecture still under evaluation
    # (Playwright vs Selenium vs hybrid, CDP, remote browser, WebSocket sessions).
    # Present so the enum and registry are ready for them, NOT implemented.
    WEB_APPLICATION = "web_application"
    INTERACTIVE_SESSION = "interactive_session"

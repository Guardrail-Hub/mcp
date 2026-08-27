"""Concrete ZAP operation handlers.

Each module adapts one existing tool service to the generic
:class:`~app.services.tools.owasp_zap.execution.handler.ZapOperationHandler`
contract and exposes a ``build_*_operation`` factory returning the fully
described :class:`~app.services.tools.owasp_zap.execution.handler.ZapOperation`
for registration in the composition root.

Handlers WRAP existing services (they do not reimplement scan logic), so tool
behaviour is unchanged by the move to the generic execution layer.
"""

from app.services.tools.owasp_zap.execution.handlers.api_scan_handler import (
    ApiScanHandler,
    build_api_scan_operation,
)
from app.services.tools.owasp_zap.execution.handlers.api_scenario_handler import (
    ApiScenarioHandler,
    build_api_scenario_operation,
)
from app.services.tools.owasp_zap.execution.handlers.api_suite_handler import (
    ApiSuiteHandler,
    build_api_suite_operation,
)

__all__ = [
    "ApiScanHandler",
    "ApiScenarioHandler",
    "ApiSuiteHandler",
    "build_api_scan_operation",
    "build_api_scenario_operation",
    "build_api_suite_operation",
]

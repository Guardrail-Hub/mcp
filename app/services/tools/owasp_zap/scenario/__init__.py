"""Scenario workflow engine — pure orchestration logic for scan_api_scenario.

Kept free of any OWASP ZAP / network dependency so the ordering, placeholder
resolution and variable/cookie/JWT propagation are unit-testable in isolation.
The service (``app/services/tools/owasp_zap/scan_api_scenario.py``) composes this
with the reused ZAP scanning primitives.
"""

from app.services.tools.owasp_zap.scenario.context import (
    ScenarioContext,
    ScenarioStepError,
    dotted_get,
    resolve_execution_order,
)

__all__ = [
    "ScenarioContext",
    "ScenarioStepError",
    "dotted_get",
    "resolve_execution_order",
]

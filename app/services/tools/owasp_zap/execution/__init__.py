"""Generic ZAP execution layer.

This package is the tool-agnostic seam between the background Dispatcher and the
concrete ZAP tools. The Dispatcher depends only on the abstractions defined here
(``ZapOperationType``, ``ExecutionStrategy``, ``ZapOperation`` /
``ZapOperationHandler`` and ``ZapOperationRegistry``) and never on any specific
scan service. Adding a new ZAP tool means registering a new ``ZapOperation`` in
the composition root — no Dispatcher or WorkerService change.

See ``architecture/zap-generic-execution/DESIGN.md``.
"""

from app.services.tools.owasp_zap.execution.execution_strategy import ExecutionStrategy
from app.services.tools.owasp_zap.execution.handler import (
    ZapOperation,
    ZapOperationHandler,
)
from app.services.tools.owasp_zap.execution.operation_type import ZapOperationType
from app.services.tools.owasp_zap.execution.registry import (
    UnknownOperationTypeError,
    ZapOperationRegistry,
)

__all__ = [
    "ExecutionStrategy",
    "UnknownOperationTypeError",
    "ZapOperation",
    "ZapOperationHandler",
    "ZapOperationRegistry",
    "ZapOperationType",
]

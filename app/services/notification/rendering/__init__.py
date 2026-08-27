"""How a notification reads — internal to the notification package.

Owns both halves of that responsibility: :class:`OperationProgress`, the
transport- and tool-neutral snapshot describing what happened, and
:class:`OperationNotificationRenderer`, which turns that snapshot into the
status card a channel adapter delivers.

The snapshot lives here because being rendered is its entire purpose — it is
the render model, not a domain model. (Domain models live in ``app.domain``;
keeping the two apart is why this package is named for its responsibility
rather than for the kind of Python object it holds.)

Pure presentation: no orchestration, no I/O.
"""

from app.services.notification.rendering.operation_progress import OperationProgress
from app.services.notification.rendering.renderer import (
    OperationNotificationRenderer,
    humanize_stage,
)

__all__ = [
    "OperationNotificationRenderer",
    "OperationProgress",
    "humanize_stage",
]

"""Notification dispatch — internal to the notification package.

The capability-aware delivery strategy (:class:`OperationNotifier`): decides
between message updates and new messages, applies throttling, and keeps
per-operation presentation state. Constructed only by the composition root.
"""

from app.services.notification.dispatch.operation_notifier import OperationNotifier

__all__ = [
    "OperationNotifier",
]

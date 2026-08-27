"""Notification context — public API.

External application modules depend only on the send-a-notification contract
exported here: build a :class:`Notification` and hand it to the
:class:`NotificationService`. Everything else in this package (delivery
strategy, rendering, throttling policy, progress models, event subscribers)
is internal implementation. Only the composition root (``app/bootstrap.py``)
may import those internals directly — see the Composition Root Exception in
``services-architecture-plan.md``.
"""

from app.services.notification.message.notification import Notification
from app.services.notification.notification_service import (
    NotificationService,
    UnknownChannelError,
)

__all__ = [
    "Notification",
    "NotificationService",
    "UnknownChannelError",
]

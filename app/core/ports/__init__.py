"""Cross-domain hexagonal ports (interfaces).

These are the outbound/driving contracts the application depends on. Persistence
is intentionally NOT here — it already has its abstraction in
``app.dao.base.BaseOperationDAO`` and must not be duplicated.
"""

from app.core.ports.event_publisher import DomainEvent, EventPublisher
from app.core.ports.notification_channel import (
    ChannelCapabilities,
    MessageRef,
    NotificationChannel,
)

__all__ = [
    "ChannelCapabilities",
    "DomainEvent",
    "EventPublisher",
    "MessageRef",
    "NotificationChannel",
]

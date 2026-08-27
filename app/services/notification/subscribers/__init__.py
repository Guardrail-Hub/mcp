"""Notification subscribers — internal to the notification package.

Glue that turns configured domain events into notifications. Constructed only
by the composition root.
"""

from app.services.notification.subscribers.event_subscriber import (
    EventNotificationSubscriber,
)

__all__ = [
    "EventNotificationSubscriber",
]

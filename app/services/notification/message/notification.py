"""Notification message — the minimal request the NotificationService delivers.

Intentionally small (YAGNI): richer routing (preferences, severity, templates,
subscribing to domain events) is added when an event source requires it.
"""

from dataclasses import dataclass
from typing import Optional

from app.core.ports.notification_channel import NotificationContent


@dataclass(frozen=True, slots=True)
class Notification:
    """A single message to deliver through one named channel.

    Carries the message in two forms: the plain-text body every transport can
    deliver, and — when the caller has it — the structured content behind it, so
    a rich adapter can render natively instead of recovering structure from
    text.

    Attributes:
        channel: Target channel name (e.g. ``"slack"``). Matches
            :attr:`NotificationChannel.name`.
        destination: Channel-native destination (e.g. a Slack channel id).
        message: The plain-text message body (also the rich-transport fallback).
        content: Optional structured content behind *message*.
    """

    channel: str
    destination: str
    message: str
    content: Optional[NotificationContent] = None

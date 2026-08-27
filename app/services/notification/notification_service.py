"""Notification application service.

Decides *which channel* handles a notification and delegates actual delivery to
that channel's adapter. It owns routing, not delivery: it must never contain a
Slack/Discord SDK call. Richer decisioning (preferences, severity, templates,
subscribing to domain events) is added when an event source requires it (YAGNI).
"""

from typing import Iterable

from app.core.ports.notification_channel import (
    ChannelCapabilities,
    MessageRef,
    NotificationChannel,
)
from app.services.notification.message.notification import Notification


class UnknownChannelError(LookupError):
    """Raised when a notification targets a channel that is not registered."""


class NotificationService:
    """Routes a notification to a registered channel and delegates delivery.

    Owns routing and capability lookup, never delivery mechanics: it must contain
    no Slack/Discord SDK call. Whether to send a new message or update an existing
    one is a *strategy* decision made upstream (by the notifier) from the
    capabilities this service exposes — the service just forwards to the adapter.
    """

    def __init__(self, channels: Iterable[NotificationChannel]) -> None:
        """
        Args:
            channels: The delivery channels this service can route to. Each is
                keyed by its :attr:`NotificationChannel.name`.
        """
        self._channels: dict[str, NotificationChannel] = {c.name: c for c in channels}

    @property
    def channels(self) -> tuple[str, ...]:
        """Names of the registered channels."""
        return tuple(self._channels)

    def capabilities(self, channel: str) -> ChannelCapabilities:
        """Return the declared capabilities of a registered channel.

        Raises:
            UnknownChannelError: If no registered channel matches *channel*.
        """
        return self._require(channel).capabilities

    def notify(self, notification: Notification) -> MessageRef:
        """Deliver *notification* as a new message through its target channel.

        Args:
            notification: The message to deliver.

        Returns:
            A :data:`MessageRef` handle to the delivered message (for a later
            :meth:`update`), or ``None`` if the channel provides none.

        Raises:
            UnknownChannelError: If no registered channel matches
                ``notification.channel``.
            Exception: Any delivery error raised by the channel adapter
                propagates to the caller.
        """
        channel = self._require(notification.channel)
        return channel.send(
            notification.destination, notification.message, notification.content
        )

    def update(self, notification: Notification, ref: MessageRef) -> MessageRef:
        """Edit the message identified by *ref* in place on its target channel.

        Args:
            notification: The channel/destination and the new message body.
            ref: The handle a previous :meth:`notify` returned.

        Returns:
            A :data:`MessageRef` for the (still-updatable) message.

        Raises:
            UnknownChannelError: If no registered channel matches
                ``notification.channel``.
            NotImplementedError: If the channel does not support updates.
        """
        channel = self._require(notification.channel)
        return channel.update(
            notification.destination, ref, notification.message, notification.content
        )

    def _require(self, channel: str) -> NotificationChannel:
        found = self._channels.get(channel)
        if found is None:
            raise UnknownChannelError(channel)
        return found

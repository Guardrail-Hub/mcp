"""Turn selected domain events into notifications.

Generic glue for the notification context: given a mapping of
``event_name -> message template``, it renders a message from each matching
:class:`DomainEvent` and hands a :class:`Notification` to the
:class:`NotificationService`.

It is deliberately unaware of the *operation* domain — the composition root
supplies which events map to which messages, so the notification context stays
decoupled from other domains.
"""

from typing import Mapping

from app.core.ports.event_publisher import DomainEvent
from app.core.ports.notification_channel import NotificationContent
from app.services.notification.message.notification import Notification
from app.services.notification.notification_service import NotificationService


class EventNotificationSubscriber:
    """Renders configured domain events into notifications on one channel."""

    def __init__(
        self,
        notification_service: NotificationService,
        channel: str,
        destination: str,
        templates: Mapping[str, str],
    ) -> None:
        """
        Args:
            notification_service: Where rendered notifications are delivered.
            channel: Target channel name (e.g. ``"slack"``).
            destination: Channel-native destination (e.g. a Slack channel id).
            templates: ``event_name -> str.format template``. Only events whose
                name is a key here produce a notification.
        """
        self._service = notification_service
        self._channel = channel
        self._destination = destination
        self._templates = dict(templates)

    @property
    def event_names(self) -> tuple[str, ...]:
        """The event names this subscriber reacts to."""
        return tuple(self._templates)

    def handle(self, event: DomainEvent) -> None:
        """Render *event* (if configured) and deliver it as a notification.

        Unconfigured events are ignored. The message template is formatted with
        the event payload.
        """
        template = self._templates.get(type(event).name)
        if template is None:
            return
        # ``as_mapping`` exists solely because str.format needs a mapping; the
        # bus itself carries typed events end to end.
        message = template.format(**event.as_mapping())
        self._service.notify(
            Notification(
                channel=self._channel,
                destination=self._destination,
                message=message,
                # A rendered template is a single headline with no further
                # structure, so the structured form is just that title. Supplied
                # explicitly so no transport has to infer it from the text.
                content=NotificationContent(title=message),
            )
        )

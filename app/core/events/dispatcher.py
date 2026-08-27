"""In-process event dispatcher.

The MVP transport for domain events: a synchronous, in-memory publish/subscribe
mechanism living inside the single application process. It implements the
:class:`~app.core.ports.event_publisher.EventPublisher` port, so application
code depends on the contract, not on this concrete transport. When a
durable/broker transport is introduced in a later stage it implements the same
port with no change to publishers.
"""

from collections import defaultdict
from typing import Callable, DefaultDict, List

from app.core.mcp_logger import MCPLogger
from app.core.ports.event_publisher import DomainEvent, EventPublisher

EventHandler = Callable[[DomainEvent], None]


class InProcessEventDispatcher(EventPublisher):
    """Synchronous, in-memory pub/sub for domain events.

    Subscribers register by event name; publishing invokes every matching
    handler in registration order. A failing handler is logged and skipped so
    one subscriber cannot break delivery to the others, and :meth:`publish`
    never raises back to the caller.
    """

    def __init__(self) -> None:
        self._handlers: DefaultDict[str, List[EventHandler]] = defaultdict(list)
        self._logger = MCPLogger("events")

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register *handler* to receive events published under *event_name*.

        Args:
            event_name: The event contract's :attr:`DomainEvent.name` — read
                off the event class (e.g. ``OperationCreated.name``) rather than
                written as a literal.
            handler: A callable invoked with the published typed event.
        """
        self._handlers[event_name].append(handler)

    def publish(self, event: DomainEvent) -> None:
        """Deliver *event* to every handler subscribed to its name (best-effort).

        Args:
            event: The typed domain event to dispatch. Its routing key is the
                class-level :attr:`DomainEvent.name`, so the event itself
                decides where it goes — no caller repeats a string literal.
                Handlers are invoked in registration order; any handler
                exception is logged and does not propagate.
        """
        for handler in list(self._handlers.get(type(event).name, ())):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 - isolate subscriber failures
                self._logger.error(
                    f"event handler failed for '{event.name}': {exc}"
                )

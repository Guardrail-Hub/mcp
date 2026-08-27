"""EventPublisher port — the outbound boundary for emitting domain events.

This is a cross-domain hexagonal port. Application code depends on the
*contract* of publishing an event, never on a concrete transport. At MVP the
only implementation is the in-process dispatcher
(``app.core.events.dispatcher.InProcessEventDispatcher``); a durable/broker
transport can implement the same port later with no change to publishers.

:class:`DomainEvent` is the abstract envelope; concrete, **typed** event
contracts live in :mod:`app.core.events.contracts`. The base carries no
domain vocabulary, so any part of the application can publish or subscribe
without coupling to another context's service — it depends on the event
contract, which belongs to neither side.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """Base class for an immutable record that something happened.

    Concrete events are frozen dataclasses that declare their own **typed**
    fields — there is no free-form payload dictionary. A subscriber reads
    ``event.stage``, not ``event.payload["stage"]``, so a producer/consumer
    mismatch is a name error at import time rather than a silent ``None`` at
    runtime.

    Subclasses set two class-level attributes:

    * ``name`` — the stable routing key subscribers register against
      (e.g. ``"operation.created"``). Declared once on the class so no caller
      ever repeats the string literal.
    * ``version`` — the contract version. Bump it when a field's meaning
      changes incompatibly; adding an optional field is a compatible change and
      does not.

    Attributes:
        occurred_at: UTC timestamp of when the event occurred.
    """

    name: ClassVar[str] = "domain.event"
    version: ClassVar[int] = 1

    occurred_at: datetime = field(default_factory=_utcnow)

    def as_mapping(self) -> Dict[str, Any]:
        """Return the event's typed fields as a plain mapping.

        Provided **only** for string templating and logging, where a mapping is
        the required input format. It is never how a subscriber reads an event —
        the bus itself carries typed objects end to end.
        """
        return {f.name: getattr(self, f.name) for f in fields(self)}


class EventPublisher(ABC):
    """Contract for publishing a domain event to interested subscribers."""

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish a domain event.

        Args:
            event: A concrete :class:`DomainEvent` instance. It is delivered to
                every subscriber registered for its class-level ``name``.
                Delivery is best-effort: an individual subscriber failure must
                not raise back to the caller.
        """

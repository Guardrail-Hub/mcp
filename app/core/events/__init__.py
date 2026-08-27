"""In-process domain event transport (MVP).

Exposes the concrete in-process dispatcher. The event message and publisher
*contract* live with the port in ``app.core.ports.event_publisher``.
"""

from app.core.events.dispatcher import EventHandler, InProcessEventDispatcher

__all__ = ["EventHandler", "InProcessEventDispatcher"]

"""What gets delivered — the notification message itself.

Owns :class:`Notification`: the request to deliver one message through one
named channel. It is the notification context's central vocabulary — the thing
the service accepts, the subscribers build, and the dispatch strategy sends.

Distinguished from its neighbours by responsibility, not by Python construct:
``rendering/`` decides *how* a message reads, ``policy/`` decides *whether* to
send one, ``dispatch/`` decides *how it is delivered*, and this package defines
*what a message is*.
"""

from app.services.notification.message.notification import Notification

__all__ = ["Notification"]

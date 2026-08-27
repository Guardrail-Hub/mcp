"""Notification policies — internal to the notification package.

Pure decision logic with no delivery side effects. Currently the progress
throttle (stage-change / percent-step / interval gate).
"""

from app.services.notification.policy.throttle import ProgressThrottle

__all__ = [
    "ProgressThrottle",
]

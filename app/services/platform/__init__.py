"""Platform context — public API.

Exposes the platform lifecycle use-case entry points (:func:`report_startup`,
:func:`report_shutdown`) and the :class:`PlatformReporter` they are documented
against. Rendering helpers and channel defaults are internal.
"""

from app.services.platform.lifecycle import report_shutdown, report_startup
from app.services.platform.platform_reporter import PlatformReporter

__all__ = [
    "PlatformReporter",
    "report_shutdown",
    "report_startup",
]

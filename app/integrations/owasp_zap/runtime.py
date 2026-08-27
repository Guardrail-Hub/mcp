"""
Process-wide ZAP worker-pool runtime singletons.

Mirrors the existing ``settings`` module-singleton pattern (`app/core/config`):
the Worker Registry, Scheduler, and Pool Manager are constructed once per
process and shared by everything that touches the ZAP pool — the worker
registration/heartbeat router, the scan service, and the queue dispatcher all
import the *same* instances from here rather than each constructing their
own. Without this, the router could register a worker into a registry the
dispatcher never looks at.

The dispatcher itself is *not* a singleton here because it owns a background
thread with an explicit start/stop lifecycle tied to the app's startup
sequence — see ``app.bootstrap.build_zap_dispatcher`` and
``app/core/startup.py``.
"""

from app.core.config import settings
from app.integrations.owasp_zap.pool import OwaspZapPoolManager
from app.integrations.owasp_zap.registry import OwaspZapWorkerRegistry
from app.integrations.owasp_zap.scheduler import RoundRobinScheduler

registry = OwaspZapWorkerRegistry()
scheduler = RoundRobinScheduler()
pool_manager = OwaspZapPoolManager(
    registry=registry, scheduler=scheduler, api_key=settings.zap_api_key
)

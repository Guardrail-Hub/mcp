"""Process-wide JMeter worker-pool runtime singletons.

Mirrors ``app/integrations/owasp_zap/runtime.py``: the Worker Registry,
Scheduler and Pool Manager are constructed once per process and shared by
everything that touches the JMeter pool — the worker registration/heartbeat
router and the queue dispatcher import the *same* instances from here rather
than constructing their own. Without this, the router could register a worker
into a registry the dispatcher never looks at.

These are JMeter's own instances. The equivalents in
``app/integrations/owasp_zap/runtime.py`` are ZAP's; the two pools share no
object, so a worker can never cross-register between them.

**Naming.** This module is the *process singleton* runtime. The per-operation
orchestrator is ``JMeterRuntime`` in
``app/services/tools/jmeter/execution/runtime.py`` — the layer distinguishes
them, per ``architecture/jmeter-engine/runtime-responsibility.md`` §1.

The dispatcher is deliberately not a singleton here: it owns background threads
with an explicit start/stop lifecycle tied to the app's startup sequence — see
``app.bootstrap.build_jmeter_dispatcher`` and ``app/core/startup.py``.
"""

from app.core.config import settings
from app.integrations.jmeter.pool import JMeterWorkerPoolManager
from app.integrations.jmeter.registry import JMeterWorkerRegistry
from app.integrations.jmeter.scheduler import RoundRobinJMeterWorkerScheduler

registry = JMeterWorkerRegistry()
scheduler = RoundRobinJMeterWorkerScheduler()
pool_manager = JMeterWorkerPoolManager(
    registry=registry,
    scheduler=scheduler,
    request_timeout_seconds=settings.jmeter_worker_request_timeout_seconds,
)

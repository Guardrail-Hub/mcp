class JMeterMixin:
    # =====================================================
    # JMeter Worker Registry / Pool Manager
    # =====================================================
    # As with ZAP, there is intentionally no worker-name / prefix / count
    # configuration here. Workers self-register at runtime via
    # POST /jmeter-workers/register and the Worker Registry is the single
    # source of truth for the pool — see app/integrations/jmeter/registry.py.
    #
    # There is also no JMeter version setting: the engine version is a property
    # of the worker's Docker image and is never selected by the server or the
    # caller.

    # A worker that hasn't sent a heartbeat within this many seconds is
    # considered Offline: no new operations are assigned to it, and any
    # operation it was executing is marked FAILED (no automatic retry).
    #
    # Deliberately more generous than ZAP's 60s. A JMeter worker under full
    # load is saturating its own CPU and network to generate that load, so its
    # heartbeat can be delayed by exactly the work it was asked to do — the
    # opposite of an idle scanner daemon. Reaping it mid-run would fail a
    # legitimate test.
    jmeter_worker_heartbeat_timeout_seconds: int = 120

    # How often the background sweeper checks every registered worker's
    # last_heartbeat against the timeout above.
    jmeter_worker_heartbeat_sweep_interval_seconds: float = 15.0

    # How often the dispatcher polls the JMeter queue lane for QUEUED work.
    # Slower than ZAP's 2s on purpose: load tests are minutes long and arrive
    # far less often than scans, so a tighter loop would only add idle queries.
    jmeter_queue_poll_interval_seconds: float = 5.0

    # Transport timeout for a single HTTP request from the server to a worker
    # agent (assignment hand-off, state query). A connection-level protection,
    # NOT a run-duration limit — a load test runs far longer than this.
    jmeter_worker_request_timeout_seconds: float = 30.0

    # =====================================================
    # JMeter execution
    # =====================================================
    # Command the worker invokes. A bare name resolved on PATH, so the Docker
    # image decides which JMeter runs — see docker/jmeter/Dockerfile. This is
    # NOT a version selector; it exists so a non-standard image can point at a
    # different install path.
    jmeter_binary: str = "jmeter"

    # Where the worker builds each run's workspace (plan, results, report).
    # Shared with the mcp-server so generated reports are servable from
    # /public/** without an upload step — see docker-compose.yml.
    jmeter_workspace_dir: str = "./public/report/jmeter/detail-report"

    # Hard ceiling on one JMeter process. Unlike a ZAP active scan (which has
    # no overall limit because its duration is unpredictable), a load test's
    # duration is *declared* by the caller — ramp_up + hold — so anything far
    # past that is a hung process, not a slow one. Runtime derives the actual
    # timeout from the request and clamps it to this.
    jmeter_max_run_seconds: int = 7800

    # Grace period between SIGTERM and SIGKILL when a run is cancelled or times
    # out, so JMeter can flush the JTL it has written so far.
    jmeter_terminate_grace_seconds: float = 15.0

    # How often the Runtime polls the worker for execution state.
    jmeter_execution_poll_interval_seconds: float = 5.0

    # Whether run_jmeter accepts a caller-supplied .jmx test plan.
    #
    # OFF by default, deliberately. A JMeter plan is executable content: it can
    # carry JSR223/BeanShell scripts and OS process samplers, which run on the
    # worker. Validation rejects those elements and any host other than the
    # authorized target, but that is a denylist over a large format — a weaker
    # guarantee than generating the plan ourselves. Enable only where callers
    # are trusted, and review the validator first.
    jmeter_allow_supplied_plan: bool = False

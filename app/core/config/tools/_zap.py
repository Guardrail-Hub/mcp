class ZapMixin:
    # =====================================================
    # OWASP ZAP
    # =====================================================
    # There is intentionally no worker-name / prefix / count configuration
    # here (no ZAP_WORKERS, no hostname array, no prefix+index generation).
    # Workers self-register at runtime via POST /workers/register and the
    # Worker Registry is the single source of truth for the pool — see
    # app/integrations/owasp_zap/registry.py. Only genuinely deployment-wide
    # settings live in this mixin.

    zap_api_key: str = "change-this-zap-key"

    # Health check: how many consecutive successes a worker's own startup probe
    # should require before it registers itself (enforced worker-side, by the
    # registration script — see docker/owasp_zap/register-worker.sh).
    zap_ready_stable_success_count: int = 3

    # Phase timeouts for the naturally-bounded phases (a spider crawl and the
    # passive-scan queue both terminate on their own), kept as safety limits.
    zap_spider_timeout_seconds: int = 300
    zap_passive_scan_timeout_seconds: int = 120

    # DEPRECATED — the Active Scan no longer has a fixed overall duration limit.
    # Enterprise Active Scans (and slow rules such as timing-based SQL-injection
    # checks) can legitimately run for hours, so elapsed time alone is never
    # treated as a failure. This field is retained only for backward
    # compatibility with existing configs/env and is NOT used to terminate a
    # scan; Active Scan liveness is governed by the health model below (see
    # ``ZapClient.wait_for_active_scan``).
    zap_scan_timeout_seconds: int = 600

    # ── Active Scan health model (replaces the removed overall timeout) ──────
    # Stall detection: abort the Active Scan only if it makes NO forward
    # progress AND ZAP issues no new requests for this long. A slow-but-active
    # scan keeps ZAP's request counter moving and so keeps resetting this
    # window — it is NOT a stall. Completely rule-agnostic.
    zap_scan_stall_timeout_seconds: int = 300

    # ZAP responsiveness grace: how long ZAP's API may fail to respond
    # (sustained — transient blips are tolerated) before the scan is aborted as
    # unrecoverable. This is scan-health monitoring, not a duration limit.
    zap_scan_unresponsive_timeout_seconds: int = 120

    # Communication (transport) timeout for a single HTTP request to ZAP — a
    # connection/socket-level protection, NOT a scan-duration limit. It bounds
    # each health poll so a hung ZAP is caught by the responsiveness grace above.
    zap_request_timeout_seconds: float = 30.0

    # =====================================================
    # Worker Registry / Pool Manager
    # =====================================================
    # A worker that hasn't sent a heartbeat within this many seconds is
    # considered Offline: no new operations are assigned to it, and any
    # operation it was executing is marked FAILED (no automatic retry).
    zap_worker_heartbeat_timeout_seconds: int = 60

    # How often the background sweeper checks every registered worker's
    # last_heartbeat against the timeout above.
    zap_worker_heartbeat_sweep_interval_seconds: float = 10.0

    # How often the Pool Manager polls the Operations table for QUEUED
    # operations and tries to assign them to an idle worker.
    zap_queue_poll_interval_seconds: float = 2.0

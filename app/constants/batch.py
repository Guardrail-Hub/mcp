class BatchType:
    """Category of the tool that produced an operation.

    Also the **queue lane discriminator**: each engine's dispatcher polls only
    the batch types it owns, so two engines can share the Operations table
    without consuming each other's rows (ADR-0011).
    """

    # OWASP ZAP lane.
    API_SCAN = "api_scan"
    WEB_SCAN = "web_scan"
    INTERACTIVE_SCAN = "interactive_scan"
    BATCH_SCAN = "batch_scan"
    SUITE_SCAN = "suite_scan"

    # JMeter lane.
    JMETER_TEST = "jmeter_test"

    UNKNOWN = "unknown"

    # ── Queue lanes ──────────────────────────────────────────────────────────
    # Every dispatchable batch type belongs to exactly one lane. Adding a member
    # above without adding it to a lane makes it undispatchable — no dispatcher
    # would ever see it — which tests/dao/test_queue_lanes.py fails on.

    ALL_SCAN_TYPES: frozenset[str] = frozenset(
        {API_SCAN, WEB_SCAN, INTERACTIVE_SCAN, BATCH_SCAN, SUITE_SCAN}
    )
    ALL_JMETER_TYPES: frozenset[str] = frozenset({JMETER_TEST})

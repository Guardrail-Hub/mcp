"""The JMeter result contract persisted to ``operation.result``.

Ratified by ADR-0010 and specified in
``architecture/jmeter-engine/result-contract.md``. Four sections — execution
summary, execution metrics, generated artifacts, execution metadata — and
nothing else.

Deliberately absent, each for a recorded reason:

* ``status`` / ``error`` — the Operation owns lifecycle state (ADR-0008 §3.1,
  invariants I2/I3). On failure ``OperationService.fail()`` sets
  ``operation.error`` and ``result`` stays ``None``.
* SLA verdict / thresholds / pass-fail — that is ``Policy`` (ADR-0008 §6),
  whose trigger has not fired. Metrics are reported; judgment is not encoded.
* Findings and severities — a load test produces none (ADR-0008 §2.4).
* Per-sample time series — it already lives in the linked ``.jtl`` and the HTML
  dashboard; duplicating it into a database row has no consumer.

This is persisted data: adding a field is safe, removing or renaming one breaks
stored results and requires an ADR superseding ADR-0010.

**Amended 2026-07-29 (Phase 3).** ``summary`` and ``metrics`` are optional.
Both are now populated by ``JMeterParser`` on the normal path — but they stay
optional, and the reason has changed since the relaxation was first made.

Originally they were optional because parsing was unimplemented. Now they are
optional because parsing is the one pipeline phase whose failure must not fail
the operation: a run that executed and wrote artifacts is a real result even
when its results file cannot be read back. Recording the run with both sections
absent is truthful; zero-filling them would assert measurements nobody took.
So the relaxation outlived the milestone that motivated it, on a different and
narrower justification. It diverges from ADR-0010 §2 and is recorded in
``architecture/jmeter-engine/result-contract.md`` §1.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class JMeterLatency(BaseModel):
    """Response-time distribution for a set of samples, in milliseconds."""

    min_ms: float = Field(..., description="Fastest sample.")
    max_ms: float = Field(..., description="Slowest sample.")
    mean_ms: float = Field(..., description="Arithmetic mean response time.")
    p50_ms: float = Field(..., description="Median response time.")
    p90_ms: float = Field(..., description="90th percentile response time.")
    p95_ms: float = Field(..., description="95th percentile response time.")
    p99_ms: float = Field(..., description="99th percentile response time.")


class JMeterLabelMetrics(BaseModel):
    """Metrics for one sampler label — one request in the test plan.

    JMeter's ``label`` is a mandatory JTL column, so a plan with several
    samplers produces several of these. Without them "which endpoint is slow"
    is unanswerable.
    """

    label: str = Field(..., description="Sampler label as it appears in the JTL.")
    sample_count: int = Field(..., description="Samples recorded for this label.")
    error_count: int = Field(..., description="Failed samples for this label.")
    error_rate_percent: float = Field(
        ..., description="Failed samples as a percentage of this label's samples."
    )
    latency: JMeterLatency = Field(..., description="Response times for this label.")
    throughput_rps: float = Field(
        ..., description="Completed samples per second for this label."
    )


class JMeterExecutionSummary(BaseModel):
    """What happened, at a glance."""

    target_url: str = Field(..., description="The target the plan addressed.")
    total_samples: int = Field(..., description="Samples recorded across the run.")
    error_count: int = Field(..., description="Samples that failed.")
    error_rate_percent: float = Field(
        ..., description="Failed samples as a percentage of all samples."
    )
    duration_seconds: float = Field(
        ..., description="Wall-clock duration of the test execution."
    )


class JMeterMetrics(BaseModel):
    """Aggregate metrics for the whole run, plus the per-label breakdown."""

    latency: JMeterLatency = Field(..., description="Response times across all samples.")
    throughput_rps: float = Field(..., description="Completed samples per second.")
    received_kb_per_sec: float = Field(..., description="Inbound throughput.")
    sent_kb_per_sec: float = Field(..., description="Outbound throughput.")
    labels: list[JMeterLabelMetrics] = Field(
        default_factory=list, description="Per-sampler breakdown."
    )


class JMeterArtifacts(BaseModel):
    """Public links to the files this run produced.

    Served by the existing ``/public/**`` static mount from
    ``ReportPaths.JMETER_DETAIL_DIR/{operation_id}/``. There is no artifact
    store component — this follows the same mechanism ZAP reports use.

    Every field here is a **link**; the sibling ``JMeterTestResult.summary`` is
    **measurements**. ``summary_page`` carries the ``_page`` suffix so that
    distinction survives being read aloud, quoted in a log line, or skimmed in
    API docs — nesting alone disambiguated the payload but not the prose.
    """

    summary_page: Optional[str] = Field(
        None,
        description=(
            "The run's entry page: what ran, against what, with the headline "
            "numbers and links to everything else. Start here."
        ),
    )
    view_report: Optional[str] = Field(
        None, description="Open this link in your browser to view the HTML dashboard."
    )
    results_data: Optional[str] = Field(
        None, description="Raw JTL results, for import into other tools."
    )
    test_plan: Optional[str] = Field(
        None, description="The JMX test plan that was executed."
    )
    engine_log: Optional[str] = Field(
        None, description="JMeter's own engine log for this run."
    )


class JMeterExecutionMetadata(BaseModel):
    """Who ran it, when, and under which load profile.

    Deliberately records no JMeter version. The engine version is a property of
    the Docker image the worker runs, which is the single source of truth for
    it; echoing it here would create a second one that can disagree. The caller
    never selects a version, so there is nothing to record on their behalf.
    """

    worker_id: str = Field(..., description="Worker that executed the run.")
    started_at: datetime = Field(..., description="UTC start of execution.")
    finished_at: datetime = Field(..., description="UTC end of execution.")
    thread_count: int = Field(..., description="Concurrent threads (virtual users).")
    ramp_up_seconds: int = Field(
        ..., description="Seconds over which all threads were started."
    )
    hold_seconds: int = Field(
        ..., description="Seconds the full thread count was sustained."
    )


class JMeterTestResult(BaseModel):
    """The full payload persisted to ``operation.result``.

    Written by ``JMeterRuntime`` at the Persist phase; read by
    ``analyze_jmeter_result`` and ``GET /api/history/get-result``. It is the
    analyzer's **only** input — any number the analyzer needs must be produced
    by the parser and land in this model, never re-derived from the artifacts.
    """

    operation_id: str = Field(
        ..., description="Matches the operation_id returned by run_jmeter."
    )
    summary: Optional[JMeterExecutionSummary] = Field(
        None,
        description=(
            "Sample counts and error rate, read from the JTL. Absent only when "
            "the run's results file could not be read back."
        ),
    )
    metrics: Optional[JMeterMetrics] = Field(
        None,
        description=(
            "Latency, throughput and the per-label breakdown, read from the "
            "JTL. Absent only when the results file could not be read back."
        ),
    )
    artifacts: JMeterArtifacts
    metadata: JMeterExecutionMetadata

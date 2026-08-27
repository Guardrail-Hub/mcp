"""JMeterAnalyzer — what the numbers mean.

**What Analyzer owns:** backing the ``analyze_jmeter_result`` MCP tool. It loads
the persisted operation, confirms the operation is a JMeter one, handles the
FAILED case from the record's own status and error, and turns the stored
:class:`JMeterTestResult` into a human-readable reading.

**What Analyzer must never do:** re-open the JTL or the HTML report, recompute
any metric, re-run anything, or touch the worker pool. Its only input is
``operation.result``.

That last rule is load-bearing rather than stylistic: if the analyzer needs a
number, the number must be produced by the Parser and added to the result
contract (ADR-0010). "The analyzer will just read the JTL" is the boundary
violation this class exists to prevent.

**Description, not judgment.** Everything below reports what was measured and
the arithmetic relationships between those measurements. It states no verdict,
no pass/fail, no threshold and no recommendation — that is ``Policy``, whose
trigger has not fired (ADR-0008 §6). "The 95th percentile is 4x the median" is
a fact about the data; "this endpoint is too slow" is not, and does not appear
here.

**Deterministic.** The same persisted record always yields the same words. No
clock, no randomness, no model call — the response is a pure function of the
stored record, which is what makes it testable and what stops "analysis" from
drifting into invention.

**Read-only.** One DAO call, ``get_operation``. There is no write path in this
class, and the persisted result it reads is never modified.
"""

from typing import List, Optional

from app.constants.batch import BatchType
from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.operation_record import OperationRecord
from app.domain.lifecycle import OperationPhase
from app.schemas.tools.jmeter.jmeter_test_result import (
    JMeterMetrics,
    JMeterTestResult,
)
from app.schemas.tools.jmeter.run_jmeter_test import JMeterResultAnalysisResponse

#: A run whose 95th percentile is at least this many times its median is
#: described as long-tailed. This is a **descriptive** cut used to choose wording
#: about the shape of a distribution — it is not an SLA, not a threshold anyone
#: passes or fails, and it is never persisted. Both branches report identical
#: numbers; only the sentence describing their spread differs.
_LONG_TAIL_P95_TO_P50_RATIO = 2.0


class JMeterAnalyzer:
    """Interprets a completed JMeter operation's persisted result."""

    def __init__(
        self,
        operation_dao: Optional[BaseOperationDAO] = None,
        logger: Optional[MCPLogger] = None,
    ) -> None:
        """
        Args:
            operation_dao: Read-only access to the persisted operation record.
                Resolved from the configured database provider when not
                injected, matching how ``JMeterTestService`` self-wires.
            logger: Optional logger; one is created if omitted.
        """
        if operation_dao is None:
            # Imported lazily so constructing this service does not select a
            # database provider at module import time.
            from app.dao.operation_dao import get_operation_dao  # noqa: PLC0415

            operation_dao = get_operation_dao()
        self._operation_dao = operation_dao
        self._logger = logger or MCPLogger("JMeterAnalyzer")

    # ── Entry point ──────────────────────────────────────────────────────

    def analyze_jmeter_result(
        self, operation_id: str
    ) -> JMeterResultAnalysisResponse:
        """Interpret the stored result of *operation_id*.

        Named for the ``analyze_jmeter_result`` tool it backs. Not an ``init_*``
        method: it answers synchronously and starts no operation.

        Ownership is checked with the same field that isolates the queue lane:
        ``record.batch_type in BatchType.ALL_JMETER_TYPES``. No second mechanism
        is needed, and asking an analyzer to read another tool's operation is a
        client error, not a crash.

        Four outcomes are reported distinctly, because conflating them is how a
        reader draws the wrong conclusion — "no metrics" must never be mistaken
        for "no load was generated":

        1. **Not finished** — still queued or running; nothing to read yet.
        2. **Execution failed** — the run did not complete; the record's own
           error is quoted rather than reinterpreted.
        3. **Parser degradation** — the run completed and loaded the target,
           but its results file could not be read back, so measurements are
           absent while artifacts and metadata are not.
        4. **Parsed** — the full reading.

        Raises:
            LookupError: No such operation, or it belongs to another tool. The
                router maps this to 404.
        """
        record = self._load_jmeter_operation(operation_id)

        if record.status is not OperationPhase.COMPLETED:
            return self._describe_unsuccessful(record)

        result = self._stored_result(record)
        if result is None:
            return self._describe_unreadable_record(record)
        if result.summary is None or result.metrics is None:
            return self._describe_degraded_parse(result)
        return self._describe_parsed_run(result)

    # ── Loading ──────────────────────────────────────────────────────────

    def _load_jmeter_operation(self, operation_id: str) -> OperationRecord:
        """Fetch *operation_id*, refusing anything that is not this tool's."""
        record = self._operation_dao.get_operation(operation_id)
        if record is None:
            raise LookupError(f"No operation found with id '{operation_id}'.")
        if not self._is_jmeter_operation(record.batch_type):
            raise LookupError(
                f"Operation '{operation_id}' is a '{record.batch_type}' operation, "
                "not a JMeter load test. Use the tool that produced it."
            )
        return record

    def _stored_result(self, record: OperationRecord) -> Optional[JMeterTestResult]:
        """Read ``record.result`` as the accepted contract, or ``None``.

        A completed operation is *expected* to carry a well-formed result, so a
        record that does not is a real inconsistency — logged, and reported to
        the caller as "cannot be read" rather than raised as a 500. The caller
        asked what happened; "the stored result is unreadable" is an answer.
        """
        if not record.result:
            return None
        try:
            return JMeterTestResult.model_validate(record.result)
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error(
                "Operation '%s' is COMPLETED but its stored result does not match "
                "the JMeter result contract: %s",
                record.operation_id,
                exc,
            )
            return None

    # ── The four outcomes ────────────────────────────────────────────────

    @staticmethod
    def _describe_unsuccessful(record: OperationRecord) -> JMeterResultAnalysisResponse:
        """Not-yet-finished, failed, or cancelled — reported, never reinterpreted."""
        if not record.status.is_terminal:
            return JMeterResultAnalysisResponse(
                operation_id=record.operation_id,
                summary=(
                    "This load test has not finished — it is currently "
                    f"{record.status.value}. There are no measurements to read "
                    "until it reaches a terminal state."
                ),
                details=None,
            )

        return JMeterResultAnalysisResponse(
            operation_id=record.operation_id,
            summary=(
                "This load test did not complete — it ended as "
                f"{record.status.value}, so no measurements were recorded."
            ),
            details=f"Recorded reason: {record.error or 'No reason was recorded.'}",
        )

    @staticmethod
    def _describe_unreadable_record(
        record: OperationRecord,
    ) -> JMeterResultAnalysisResponse:
        """Completed, but nothing conforming to the contract was persisted."""
        return JMeterResultAnalysisResponse(
            operation_id=record.operation_id,
            summary=(
                "This load test is recorded as completed, but its stored result "
                "is missing or does not match the expected format, so it cannot "
                "be read."
            ),
            details=(
                "Nothing can be concluded about the run from this record. Any "
                "artifacts the run produced are still on disk under this "
                "operation's report directory."
            ),
        )

    def _describe_degraded_parse(
        self, result: JMeterTestResult
    ) -> JMeterResultAnalysisResponse:
        """Executed, but its results file could not be read back.

        The distinction this case exists to protect: absent measurements are
        **not** evidence that the target was spared. The run happened, and saying
        so plainly is the whole reason this is separate from a failure. Nothing
        about the execution outcome is reinterpreted here.
        """
        return JMeterResultAnalysisResponse(
            operation_id=result.operation_id,
            summary=(
                "This load test ran to completion, but its results file could "
                "not be read back, so no measurements are available. The load "
                "itself was generated — the absence of numbers describes the "
                "recording, not the run."
            ),
            details="\n".join(
                [
                    "No latency, throughput or error-rate figures can be "
                    "reported, and none should be inferred from the run details "
                    "below.",
                    "",
                    *self._execution_context_lines(result),
                    *self._artifact_lines(result),
                ]
            ),
        )

    def _describe_parsed_run(
        self, result: JMeterTestResult
    ) -> JMeterResultAnalysisResponse:
        """The full reading: what was measured, and how the figures relate."""
        summary = result.summary
        metrics = result.metrics

        headline = (
            f"This load test drove {summary.target_url} with "
            f"{result.metadata.thread_count} concurrent threads and recorded "
            f"{summary.total_samples:,} samples over "
            f"{summary.duration_seconds:g}s, "
            f"{summary.error_count:,} of which failed "
            f"({summary.error_rate_percent:g}%). "
            f"Median response time was {metrics.latency.p50_ms:g} ms and "
            f"throughput averaged {metrics.throughput_rps:g} requests/second."
        )

        return JMeterResultAnalysisResponse(
            operation_id=result.operation_id,
            summary=headline,
            details="\n".join(
                [
                    *self._latency_lines(metrics),
                    *self._throughput_lines(metrics),
                    *self._error_lines(result),
                    *self._per_label_lines(metrics),
                    *self._execution_context_lines(result),
                    *self._artifact_lines(result),
                ]
            ),
        )

    # ── Observations (deterministic, derived only from persisted data) ───

    @classmethod
    def _latency_lines(cls, metrics: JMeterMetrics) -> List[str]:
        latency = metrics.latency
        return [
            "Response times",
            f"  Fastest {latency.min_ms:g} ms, slowest {latency.max_ms:g} ms, "
            f"mean {latency.mean_ms:g} ms.",
            f"  Percentiles — p50 {latency.p50_ms:g} ms, p90 {latency.p90_ms:g} ms, "
            f"p95 {latency.p95_ms:g} ms, p99 {latency.p99_ms:g} ms.",
            f"  {cls._describe_spread(latency.p50_ms, latency.p95_ms)}",
            "",
        ]

    @staticmethod
    def _describe_spread(p50_ms: float, p95_ms: float) -> str:
        """Describe the shape of the distribution, without judging it.

        The comparison is stated as a multiple so the reader applies their own
        expectation to it. Which of the two sentences is chosen changes no number
        and asserts nothing about whether the run was acceptable.
        """
        if p50_ms <= 0:
            return (
                "The median is 0 ms, so the spread between typical and slow "
                "requests cannot be expressed as a ratio."
            )
        ratio = p95_ms / p50_ms
        shape = (
            "the slowest 5% of requests took substantially longer than the "
            "typical one"
            if ratio >= _LONG_TAIL_P95_TO_P50_RATIO
            else "the slowest 5% of requests were close to the typical one"
        )
        return f"p95 is {ratio:.1f}x the median — {shape}."

    @staticmethod
    def _throughput_lines(metrics: JMeterMetrics) -> List[str]:
        return [
            "Throughput",
            f"  {metrics.throughput_rps:g} requests/second sustained; "
            f"{metrics.received_kb_per_sec:g} KB/s received, "
            f"{metrics.sent_kb_per_sec:g} KB/s sent.",
            "",
        ]

    @staticmethod
    def _error_lines(result: JMeterTestResult) -> List[str]:
        summary = result.summary
        if summary.error_count == 0:
            body = f"  Every one of the {summary.total_samples:,} samples succeeded."
        else:
            body = (
                f"  {summary.error_count:,} of {summary.total_samples:,} samples "
                f"failed ({summary.error_rate_percent:g}%). The per-sample "
                "failure messages are in the raw results file linked below."
            )
        return ["Errors", body, ""]

    @staticmethod
    def _per_label_lines(metrics: JMeterMetrics) -> List[str]:
        """Per-sampler breakdown, plus which sampler had the highest median.

        "Highest" is an ordering of measured values, not a claim that the sampler
        is a problem. With a single label the comparison says nothing, so it is
        omitted rather than dressed up as a finding.
        """
        if not metrics.labels:
            return []

        lines = ["Per request"]
        for entry in metrics.labels:
            lines.append(
                f"  {entry.label} — {entry.sample_count:,} samples, "
                f"{entry.error_rate_percent:g}% failed, "
                f"p50 {entry.latency.p50_ms:g} ms, p95 {entry.latency.p95_ms:g} ms, "
                f"{entry.throughput_rps:g} req/s."
            )

        if len(metrics.labels) > 1:
            slowest = max(metrics.labels, key=lambda entry: entry.latency.p50_ms)
            lines.append(
                f"  Highest median response time: {slowest.label} "
                f"({slowest.latency.p50_ms:g} ms)."
            )
        lines.append("")
        return lines

    @staticmethod
    def _execution_context_lines(result: JMeterTestResult) -> List[str]:
        metadata = result.metadata
        return [
            "Run details",
            f"  Worker {metadata.worker_id}, "
            f"{metadata.started_at.isoformat()} to {metadata.finished_at.isoformat()}.",
            f"  Load profile — {metadata.thread_count} threads, ramped up over "
            f"{metadata.ramp_up_seconds}s, held for {metadata.hold_seconds}s.",
            "",
        ]

    @staticmethod
    def _artifact_lines(result: JMeterTestResult) -> List[str]:
        """Link whatever the run actually produced; omit what it did not."""
        artifacts = result.artifacts
        available = [
            (label, link)
            for label, link in (
                ("HTML dashboard", artifacts.view_report),
                ("Raw results", artifacts.results_data),
                ("Executed test plan", artifacts.test_plan),
                ("Engine log", artifacts.engine_log),
            )
            if link
        ]
        if not available:
            return ["Artifacts", "  This run recorded no artifact links."]
        return ["Artifacts", *(f"  {label}: {link}" for label, link in available)]

    # ── Ownership ────────────────────────────────────────────────────────

    @staticmethod
    def _is_jmeter_operation(batch_type: str) -> bool:
        """Whether a persisted operation belongs to this tool."""
        return batch_type in BatchType.ALL_JMETER_TYPES

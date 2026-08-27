"""JMeterParser — what the numbers are.

**What Parser owns:** reading a JTL results file and producing
:class:`JMeterMetrics` plus the numeric parts of
:class:`JMeterExecutionSummary` — aggregation, percentiles, error rate,
throughput, and the per-label breakdown.

**What Parser does not own:** I/O beyond reading the file it is handed; the
database, the operation, the pool, or the network. It makes no judgment about
whether the values are good, evaluates no SLA, and produces no recommendation —
that is the Analyzer's, and the Analyzer reads the persisted result rather than
this file (``architecture/jmeter-engine/runtime-responsibility.md`` §5).

The point of that narrowness: the parser is a pure function from file to
numbers, so it is unit-testable against a fixture JTL with no JMeter, no worker
and no database. That is where the arithmetic bugs will actually be caught.

**Format.** JMeter's default CSV JTL, which is what
:class:`~app.integrations.jmeter.process.JMeterProcessRunner` asks for with
``-l``. The XML format is not supported: the runner owns the command line, so
there is exactly one format to read, and the header check below turns a config
drift into a clear error instead of silently-zero metrics.
"""

import csv
import math
from dataclasses import dataclass
from statistics import fmean
from typing import Iterable, List, Sequence, Tuple

from app.schemas.tools.jmeter.jmeter_test_result import (
    JMeterExecutionSummary,
    JMeterLabelMetrics,
    JMeterLatency,
    JMeterMetrics,
)

_BYTES_PER_KB = 1024.0


class JMeterResultsFileError(RuntimeError):
    """The JTL is missing, unreadable, or not a JMeter CSV results file."""


@dataclass(frozen=True)
class _Sample:
    """One row of the JTL — one request JMeter issued."""

    started_at_ms: int
    elapsed_ms: int
    label: str
    success: bool
    received_bytes: int
    sent_bytes: int

    @property
    def finished_at_ms(self) -> int:
        return self.started_at_ms + self.elapsed_ms


class JMeterParser:
    """Turns a JMeter JTL results file into the metrics section of the result."""

    #: Columns this parser reads. JMeter writes all of them by default; these
    #: four are required because every figure below derives from them, while the
    #: byte counters are optional and default to zero when a custom
    #: ``saveservice`` configuration omits them.
    REQUIRED_COLUMNS = ("timeStamp", "elapsed", "label", "success")

    def parse(
        self, jtl_path: str, target_url: str
    ) -> Tuple[JMeterExecutionSummary, JMeterMetrics]:
        """Read the JTL at *jtl_path* and aggregate it.

        Args:
            jtl_path: Path to the JTL (CSV) file JMeter wrote with ``-l``.
            target_url: The authorized target the plan addressed. Echoed into
                the summary — it is the one field there the JTL does not
                contain, since a JTL records sampler labels, not the request
                that authorized them.

        Returns:
            The summary counts and the aggregate + per-label metrics.

        Raises:
            JMeterResultsFileError: The file is missing, unreadable, or not a
                JMeter CSV results file. The caller (Runtime) turns that into a
                FAILED operation.
        """
        samples = self._read_samples(jtl_path)
        return (
            self._summarize(samples, target_url),
            self._aggregate_metrics(samples),
        )

    # ── Reading ──────────────────────────────────────────────────────────

    def _read_samples(self, jtl_path: str) -> List[_Sample]:
        """Parse every readable row of the JTL.

        A run terminated by timeout or cancellation is stopped with SIGTERM
        precisely so JMeter can flush what it already collected, which can leave
        a half-written final line. Such a row is skipped rather than failing the
        whole file — partial results from a long run are worth far more than an
        all-or-nothing parse.
        """
        try:
            with open(jtl_path, "r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                self._require_jmeter_header(jtl_path, reader.fieldnames)
                return [
                    sample
                    for sample in (self._to_sample(row) for row in reader)
                    if sample is not None
                ]
        except OSError as exc:
            raise JMeterResultsFileError(
                f"Could not read the JMeter results file at '{jtl_path}': {exc}"
            ) from exc

    def _require_jmeter_header(self, jtl_path: str, fieldnames) -> None:
        """Reject anything that is not a JMeter CSV results file, by its header."""
        missing = [
            column
            for column in self.REQUIRED_COLUMNS
            if column not in (fieldnames or ())
        ]
        if missing:
            raise JMeterResultsFileError(
                f"'{jtl_path}' is not a JMeter CSV results file — it is missing "
                f"the {', '.join(missing)} column(s). Expected the default CSV "
                "output of `jmeter -l`."
            )

    @staticmethod
    def _to_sample(row: dict):
        """Convert one CSV row, or return ``None`` if it cannot be read."""
        try:
            return _Sample(
                started_at_ms=int(row["timeStamp"]),
                elapsed_ms=int(row["elapsed"]),
                label=row["label"] or "",
                # JMeter writes the literal strings "true"/"false".
                success=str(row["success"]).strip().lower() == "true",
                received_bytes=int(row.get("bytes") or 0),
                sent_bytes=int(row.get("sentBytes") or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    # ── Aggregation ──────────────────────────────────────────────────────

    def _summarize(
        self, samples: Sequence[_Sample], target_url: str
    ) -> JMeterExecutionSummary:
        error_count = self._count_errors(samples)
        return JMeterExecutionSummary(
            target_url=target_url,
            total_samples=len(samples),
            error_count=error_count,
            error_rate_percent=self._error_rate_percent(error_count, len(samples)),
            duration_seconds=self._measured_window_seconds(samples),
        )

    def _aggregate_metrics(self, samples: Sequence[_Sample]) -> JMeterMetrics:
        window_seconds = self._measured_window_seconds(samples)
        return JMeterMetrics(
            latency=self._latency(sample.elapsed_ms for sample in samples),
            throughput_rps=self._per_second(len(samples), window_seconds),
            received_kb_per_sec=self._kb_per_second(
                sum(sample.received_bytes for sample in samples), window_seconds
            ),
            sent_kb_per_sec=self._kb_per_second(
                sum(sample.sent_bytes for sample in samples), window_seconds
            ),
            labels=self._per_label_metrics(samples),
        )

    def _per_label_metrics(
        self, samples: Sequence[_Sample]
    ) -> List[JMeterLabelMetrics]:
        """One entry per sampler label, in first-seen order.

        Insertion order rather than alphabetical: it mirrors the order the
        samplers appear in the plan, which is how whoever wrote the plan thinks
        about it.
        """
        by_label: dict = {}
        for sample in samples:
            by_label.setdefault(sample.label, []).append(sample)

        return [
            self._metrics_for_label(label, label_samples)
            for label, label_samples in by_label.items()
        ]

    def _metrics_for_label(
        self, label: str, samples: Sequence[_Sample]
    ) -> JMeterLabelMetrics:
        error_count = self._count_errors(samples)
        return JMeterLabelMetrics(
            label=label,
            sample_count=len(samples),
            error_count=error_count,
            error_rate_percent=self._error_rate_percent(error_count, len(samples)),
            latency=self._latency(sample.elapsed_ms for sample in samples),
            # This label's own window, not the run's: a sampler that only ran
            # during part of the test would otherwise report a throughput
            # diluted by the time it was not running. This is what JMeter's own
            # aggregate report does.
            throughput_rps=self._per_second(
                len(samples), self._measured_window_seconds(samples)
            ),
        )

    # ── Arithmetic ───────────────────────────────────────────────────────

    @staticmethod
    def _count_errors(samples: Sequence[_Sample]) -> int:
        return sum(1 for sample in samples if not sample.success)

    @staticmethod
    def _error_rate_percent(error_count: int, total: int) -> float:
        return round(error_count / total * 100, 4) if total else 0.0

    @staticmethod
    def _measured_window_seconds(samples: Sequence[_Sample]) -> float:
        """Wall-clock span from the first request starting to the last finishing.

        Derived from the samples rather than from the Runtime's own clock so it
        measures the load itself, excluding JVM startup and HTML dashboard
        generation. Zero for an empty run, and for the degenerate case where
        every sample starts and ends inside the same millisecond.
        """
        if not samples:
            return 0.0
        first_started = min(sample.started_at_ms for sample in samples)
        last_finished = max(sample.finished_at_ms for sample in samples)
        return round(max(last_finished - first_started, 0) / 1000, 3)

    @staticmethod
    def _per_second(count: int, window_seconds: float) -> float:
        """Rate over *window_seconds*, or 0.0 when there is no window to divide by."""
        return round(count / window_seconds, 4) if window_seconds > 0 else 0.0

    @staticmethod
    def _kb_per_second(total_bytes: int, window_seconds: float) -> float:
        if window_seconds <= 0:
            return 0.0
        return round(total_bytes / _BYTES_PER_KB / window_seconds, 4)

    @classmethod
    def _latency(cls, elapsed_values: Iterable[int]) -> JMeterLatency:
        """Response-time distribution over *elapsed_values*, in milliseconds.

        An empty run yields all zeros rather than ``None``: the contract's
        fields are non-optional floats, and "no samples, therefore zero" is
        already unambiguous next to ``total_samples == 0``.
        """
        ordered = sorted(elapsed_values)
        if not ordered:
            return JMeterLatency(
                min_ms=0.0,
                max_ms=0.0,
                mean_ms=0.0,
                p50_ms=0.0,
                p90_ms=0.0,
                p95_ms=0.0,
                p99_ms=0.0,
            )
        return JMeterLatency(
            min_ms=float(ordered[0]),
            max_ms=float(ordered[-1]),
            mean_ms=round(fmean(ordered), 4),
            p50_ms=cls._percentile(ordered, 50),
            p90_ms=cls._percentile(ordered, 90),
            p95_ms=cls._percentile(ordered, 95),
            p99_ms=cls._percentile(ordered, 99),
        )

    @staticmethod
    def _percentile(ordered: Sequence[int], percent: float) -> float:
        """Nearest-rank percentile of an already-sorted sequence.

        Nearest rank rather than an interpolating method: every value returned
        is a response time that was actually measured, so a reported p99 is a
        request that really took that long rather than a number sitting between
        two samples. It differs from JMeter's own dashboard by at most one
        adjacent sample, and unlike interpolation it never invents a figure.
        """
        rank = math.ceil(percent / 100 * len(ordered))
        return float(ordered[min(max(rank, 1), len(ordered)) - 1])

"""Makes a finished run's directory readable on its own.

JMeter's own dashboard is excellent at *what the numbers were* and silent about
*which run produced them*: open ``html-report/index.html`` a month later, from a
ticket attachment or an archive, and nothing on the page says which target was
driven, by how many threads, on which worker, or when. The artifacts alone
therefore do not survive being separated from the Operation record — and being
separated is exactly what happens when someone shares a run.

Two files fix that without touching anything JMeter produced:

* ``execution.json`` — the execution metadata, machine-readable. Metadata only:
  no metrics. Metrics already exist in three places (``test-results.jtl``, the
  parsed ``operation.result``, the dashboard), and a fourth copy is a fourth
  chance to disagree.
* ``summary.html`` — the same facts for a human, with links into the artifacts
  and a button through to the vendor dashboard.

**The vendor dashboard is never modified.** Nothing here writes inside
``html-report/``. Rewriting or injecting into generated HTML would make the
package's provenance ambiguous — a reader could no longer tell which parts
JMeter measured and which parts we added — and it would break silently on any
JMeter upgrade that changes the template. A separate page keeps ours ours and
theirs theirs, and the dashboard stays byte-identical to what the engine wrote.

This module renders and writes files. It makes no lifecycle decision, reads no
database, and is called once, by the Runtime, at the Persist phase.
"""

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

from app.core.mcp_logger import MCPLogger
from app.integrations.jmeter.process import JMeterProcessRunner
from app.schemas.tools.jmeter.jmeter_test_result import (
    JMeterExecutionSummary,
    JMeterMetrics,
)
from app.schemas.tools.jmeter.run_jmeter_test import JMeterTestRequest
from app.schemas.tools.jmeter.worker import JMeterWorkerInfo

EXECUTION_METADATA_FILE = "execution.json"
SUMMARY_PAGE_FILE = "summary.html"


def _isoformat(moment: datetime) -> str:
    """Serialize a timestamp the way the rest of the API already does.

    Pydantic renders UTC as a trailing ``Z`` while ``datetime.isoformat()``
    renders ``+00:00``. Both name the same instant, but a package whose
    ``execution.json`` disagrees character-for-character with the persisted
    result invites someone to conclude they describe different runs.
    """
    return moment.isoformat().replace("+00:00", "Z")


class JMeterArtifactPackageWriter:
    """Writes the two Guardrail Hub files that make a run package self-contained."""

    def __init__(self, logger: Optional[MCPLogger] = None) -> None:
        self._logger = logger or MCPLogger("JMeterArtifactPackage")

    def write(
        self,
        *,
        workspace: str,
        operation_id: str,
        request: JMeterTestRequest,
        worker: JMeterWorkerInfo,
        started_at: datetime,
        finished_at: datetime,
        status: str = "completed",
        summary: Optional[JMeterExecutionSummary] = None,
        metrics: Optional[JMeterMetrics] = None,
    ) -> None:
        """Write ``execution.json`` and ``summary.html`` into *workspace*.

        *summary* and *metrics* are the parsed figures, and are ``None`` when
        the results file could not be read — the page then says so instead of
        showing zeros. They are rendered into the page but **not** written into
        ``execution.json``, which stays metadata-only: the page is a view of
        numbers that live elsewhere, the way the dashboard is.

        Never raises. These files are a convenience layered on top of a run that
        already succeeded — failing to write them must not turn a completed load
        test into a failed operation, because the measurements are already safe
        in the artifacts and in ``operation.result``.
        """
        metadata = self._execution_metadata(
            operation_id=operation_id,
            request=request,
            worker=worker,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
        )
        try:
            directory = Path(workspace)
            (directory / EXECUTION_METADATA_FILE).write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (directory / SUMMARY_PAGE_FILE).write_text(
                self._summary_page(metadata, summary, metrics), encoding="utf-8"
            )
        except OSError as exc:
            self._logger.error(
                "Could not write the run package files for operation '%s': %s",
                operation_id,
                exc,
            )

    # ── execution.json ───────────────────────────────────────────────────

    @staticmethod
    def _execution_metadata(
        *,
        operation_id: str,
        request: JMeterTestRequest,
        worker: JMeterWorkerInfo,
        started_at: datetime,
        finished_at: datetime,
        status: str,
    ) -> dict:
        """The execution facts, and deliberately nothing else.

        No latency, throughput, sample or error figure appears here. This
        document answers "which run was this"; the metrics belong to the
        artifacts and the parsed result, which are their single source.
        """
        return {
            "operation_id": operation_id,
            "status": status,
            "target": {
                "url": request.target_url,
                "method": request.method.value,
            },
            "execution": {
                "thread_count": request.thread_count,
                "ramp_up_seconds": request.ramp_up_seconds,
                "hold_seconds": request.hold_seconds,
            },
            "worker": {
                "worker_id": worker.worker_id,
                "started_at": _isoformat(started_at),
                "finished_at": _isoformat(finished_at),
            },
        }

    # ── summary.html ─────────────────────────────────────────────────────

    @classmethod
    def _summary_page(
        cls,
        metadata: dict,
        summary: Optional[JMeterExecutionSummary],
        metrics: Optional[JMeterMetrics],
    ) -> str:
        """Render the human-facing page.

        The execution facts are read back out of *metadata* — the same dict
        written to ``execution.json`` — so the page and the JSON can never
        disagree: if the page shows a value, that value is in the JSON, because
        it came from it.

        Every interpolated value passes through :func:`html.escape` — a target
        URL is caller-supplied text being written into a document that someone
        will open in a browser, which is the exact shape of a stored XSS.
        """
        target = metadata["target"]
        execution = metadata["execution"]
        worker = metadata["worker"]

        execution_rows = [
            ("Operation ID", metadata["operation_id"]),
            ("Status", metadata["status"]),
            ("Target URL", target["url"]),
            ("HTTP Method", target["method"]),
            ("Worker ID", worker["worker_id"]),
            ("Started At", worker["started_at"]),
            ("Finished At", worker["finished_at"]),
            ("Duration", cls._wall_clock_duration(worker)),
        ]
        configuration_rows = [
            ("Thread Count", f"{execution['thread_count']} concurrent threads"),
            ("Ramp-up", f"{execution['ramp_up_seconds']}s"),
            ("Hold Time", f"{execution['hold_seconds']}s"),
        ]

        artifacts = [
            ("Open Dashboard", f"{JMeterProcessRunner.REPORT_DIR}/index.html",
             "JMeter's own HTML dashboard"),
            ("Open Engine Log", JMeterProcessRunner.ENGINE_LOG,
             "JMeter's engine log for this run"),
            ("Open Test Plan", JMeterProcessRunner.PLAN_FILE,
             "The .jmx that was executed"),
            ("Open Raw Results", JMeterProcessRunner.RESULTS_FILE,
             "Every sample, as CSV"),
            ("Open execution.json", EXECUTION_METADATA_FILE,
             "This run's metadata, machine-readable"),
        ]
        artifact_links = "\n".join(
            f'      <li><a href="{escape(href)}">{escape(label)}</a>'
            f"<span>{escape(note)}</span></li>"
            for label, href, note in artifacts
        )
        detail_rows = cls._rows(execution_rows)
        configuration_table = cls._rows(configuration_rows)
        summary_section = cls._summary_section(summary, metrics)

        # Links are relative on purpose: the package stays browsable after being
        # copied, zipped or served from anywhere, not just from the host that
        # produced it.
        dashboard = f"{JMeterProcessRunner.REPORT_DIR}/index.html"
        return cls._document(
            metadata=metadata,
            detail_rows=detail_rows,
            configuration_table=configuration_table,
            summary_section=summary_section,
            artifact_links=artifact_links,
            dashboard=dashboard,
        )

    # ── Page fragments ───────────────────────────────────────────────────

    @staticmethod
    def _rows(rows) -> str:
        return "\n".join(
            f"      <tr><th>{escape(str(label))}</th>"
            f"<td>{escape(str(value))}</td></tr>"
            for label, value in rows
        )

    @staticmethod
    def _wall_clock_duration(worker: dict) -> str:
        """End-to-end duration, which is *not* the measured load window.

        This spans everything the operation did — JVM startup, the load itself,
        and HTML dashboard generation — because it is derived from the same two
        timestamps displayed directly above it, and a Duration that disagreed
        with its own Started/Finished would be a bug report waiting to happen.
        The narrower "time the target was actually under load" is
        ``duration_seconds`` in the Summary section below.
        """
        try:
            started = datetime.fromisoformat(worker["started_at"].replace("Z", "+00:00"))
            finished = datetime.fromisoformat(worker["finished_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            return "unknown"
        seconds = (finished - started).total_seconds()
        if seconds < 0:
            return "unknown"
        minutes, remainder = divmod(int(seconds), 60)
        return f"{minutes}m {remainder}s" if minutes else f"{remainder}s"

    @classmethod
    def _summary_section(
        cls,
        summary: Optional[JMeterExecutionSummary],
        metrics: Optional[JMeterMetrics],
    ) -> str:
        """The headline numbers, or an honest statement that there are none.

        Absent figures mean the results file could not be read back — the run
        itself happened. Saying that plainly matters more than filling the table
        with zeros, which would read as "the target was never touched".
        """
        if summary is None or metrics is None:
            return (
                '    <p style="margin:0;color:#6b6b6b;">'
                "This run completed, but its results file could not be read back, "
                "so no measurements are available. The load was still generated — "
                "the missing numbers describe the recording, not the run."
                "</p>"
            )
        return "    <table>\n" + cls._rows([
            ("Total Samples", f"{summary.total_samples:,}"),
            ("Error Count", f"{summary.error_count:,}"),
            ("Error Rate", f"{summary.error_rate_percent:g}%"),
            ("Throughput", f"{metrics.throughput_rps:g} requests/second"),
            ("Mean Latency", f"{metrics.latency.mean_ms:g} ms"),
            ("p95", f"{metrics.latency.p95_ms:g} ms"),
            ("p99", f"{metrics.latency.p99_ms:g} ms"),
        ]) + "\n    </table>"

    @staticmethod
    def _document(
        *, metadata, detail_rows, configuration_table, summary_section,
        artifact_links, dashboard,
    ) -> str:

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JMeter run {escape(metadata['operation_id'])} — Guardrail Hub</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 2.5rem 1.5rem; background: #f6f6f4; color: #1a1a1a;
         font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 760px; margin: 0 auto; }}
  .eyebrow {{ font-size: 12px; letter-spacing: .08em; text-transform: uppercase;
              color: #6b6b6b; margin: 0 0 .35rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 1.5rem; font-weight: 600; }}
  section {{ background: #fff; border: 1px solid #e2e0da; border-radius: 10px;
             padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; }}
  h2 {{ font-size: .8rem; letter-spacing: .06em; text-transform: uppercase;
        color: #6b6b6b; margin: 0 0 .9rem; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: .5rem 0; border-bottom: 1px solid #f0efea;
            font-weight: 400; vertical-align: top; }}
  th {{ color: #6b6b6b; width: 40%; }}
  td {{ font-variant-numeric: tabular-nums; word-break: break-all; }}
  tr:last-child th, tr:last-child td {{ border-bottom: 0; }}
  .cta {{ display: inline-block; background: #1a1a1a; color: #fff; text-decoration: none;
          padding: .7rem 1.3rem; border-radius: 7px; font-weight: 500; }}
  .cta:hover {{ background: #333; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ padding: .5rem 0; border-bottom: 1px solid #f0efea; }}
  li:last-child {{ border-bottom: 0; }}
  li a {{ color: #1a1a1a; font-weight: 500; }}
  li span {{ display: block; color: #6b6b6b; font-size: 13px; }}
  footer {{ color: #6b6b6b; font-size: 12.5px; text-align: center; margin-top: 1.5rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #17171a; color: #ececec; }}
    section {{ background: #1f1f23; border-color: #33333a; }}
    th, td, li {{ border-color: #2a2a30; }}
    li a {{ color: #ececec; }}
    .cta {{ background: #ececec; color: #17171a; }}
  }}
</style>
</head>
<body>
<main>
  <p class="eyebrow">Guardrail Hub &middot; JMeter load test</p>
  <h1>Run {escape(metadata['operation_id'])}</h1>

  <section>
    <h2>Execution Information</h2>
    <table>
{detail_rows}
    </table>
  </section>

  <section>
    <h2>Test Configuration</h2>
    <table>
{configuration_table}
    </table>
  </section>

  <section>
    <h2>Summary</h2>
{summary_section}
  </section>

  <section>
    <h2>Full report</h2>
    <p style="margin:0 0 1rem;color:#6b6b6b;">
      Response times, throughput and the per-request breakdown are in JMeter's
      own dashboard, exactly as the engine generated it.
    </p>
    <a class="cta" href="{escape(dashboard)}">Open JMeter Dashboard</a>
  </section>

  <section>
    <h2>Available Artifacts</h2>
    <ul>
{artifact_links}
    </ul>
  </section>

  <footer>
    Generated by Guardrail Hub. The dashboard under
    <code>{escape(JMeterProcessRunner.REPORT_DIR)}/</code> is JMeter's own output
    and is left untouched.
  </footer>
</main>
</body>
</html>
"""

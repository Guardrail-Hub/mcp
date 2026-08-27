"""JMeterRuntime — the per-operation orchestrator.

**Naming.** Two things in this repository are called "runtime", distinguished by
layer, and this is the application-logic one:

* ``services/tools/<tool>/execution/runtime.py`` — per-operation orchestration
  (this file).
* ``integrations/<tool>/runtime.py`` — process-wide singletons (registry,
  scheduler, pool manager), as ``integrations/owasp_zap/runtime.py`` is.

**What Runtime owns:** the ordered pipeline below, and *all* lifecycle
persistence for its operation. It is the sole caller of ``OperationService`` for
JMeter operations — with one deliberate exception, the Dispatcher's own
``fail()`` for an operation it could never hand off.

**What Runtime does not own:** spawning or signalling an OS process (Worker),
turning a JTL into numbers (Parser), deciding whether those numbers are good
(Analyzer), or choosing which worker runs the job (Dispatcher).

The pipeline::

    Generate (optional) -> Validate -> Execute -> Parse -> Persist

Generation is skipped when the caller supplied their own plan. **Validation is
not skippable either way** — a supplied plan is checked by the same validator as
a generated one, because validation is where target authorization (Guardrail G4)
is enforced and a caller-supplied plan is exactly the case that needs it most.

Parse reads the JTL the run wrote and fills in ``summary``/``metrics``. It is
the one phase allowed to fail *without* failing the operation: a run that
executed and produced artifacts is a real result even when its numbers could
not be read back, so an unreadable JTL degrades the result rather than
discarding it. The contract makes both sections optional for exactly this case.
"""

import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from app.core.logger_utils import LoggerUtils
from app.core.mcp_logger import MCPLogger
from app.integrations.jmeter.parser import JMeterParser, JMeterResultsFileError
from app.integrations.jmeter.process import JMeterProcessRunner
from app.integrations.jmeter.worker_client import JMeterWorkerCommunicationError
from app.schemas.tools.jmeter.jmeter_test_result import (
    JMeterArtifacts,
    JMeterExecutionMetadata,
    JMeterTestResult,
)
from app.schemas.tools.jmeter.run_jmeter_test import JMeterTestRequest
from app.schemas.tools.jmeter.worker import JMeterWorkerInfo
from app.schemas.tools.jmeter.worker_assignment import (
    JMeterAssignmentRequest,
    JMeterExecutionState,
)
from app.services.operations.operation_service import OperationService
from app.services.tools.jmeter.execution.artifact_package import (
    SUMMARY_PAGE_FILE,
    JMeterArtifactPackageWriter,
)
from app.services.tools.jmeter.plan import (
    JMeterPlanGenerator,
    JMeterPlanValidationError,
    JMeterPlanValidator,
)


class JMeterRuntime:
    """Runs one JMeter operation end to end."""

    def __init__(
        self,
        operation_service: OperationService,
        pool_manager=None,
        generator: Optional[JMeterPlanGenerator] = None,
        validator: Optional[JMeterPlanValidator] = None,
        parser: Optional[JMeterParser] = None,
        package_writer: Optional[JMeterArtifactPackageWriter] = None,
        config=None,
        logger: Optional[MCPLogger] = None,
    ) -> None:
        """
        Args:
            operation_service: Lifecycle transitions and result persistence.
            pool_manager: Supplies the client for the assigned worker and takes
                the worker back when the run ends. Defaults to the process-wide
                JMeter pool.
            generator: JMX builder; injectable for tests.
            validator: Plan validator; injectable for tests.
            parser: JTL reader; injectable for tests.
            package_writer: Writes the run package's ``execution.json`` and
                ``summary.html``; injectable for tests.
            config: Settings object; defaults to ``app.core.config.settings``.
            logger: Optional logger; one is created if omitted.
        """
        if config is None:
            from app.core.config import settings as config  # noqa: PLC0415
        if pool_manager is None:
            from app.integrations.jmeter.runtime import (  # noqa: PLC0415
                pool_manager as default_pool_manager,
            )

            pool_manager = default_pool_manager

        self._operation_service = operation_service
        self._pool_manager = pool_manager
        self._generator = generator or JMeterPlanGenerator()
        self._validator = validator or JMeterPlanValidator()
        self._parser = parser or JMeterParser()
        self._package_writer = package_writer or JMeterArtifactPackageWriter()
        self._config = config
        self._logger = logger or MCPLogger("JMeterRuntime")

    # ── Entry point ──────────────────────────────────────────────────────

    def run(
        self,
        operation_id: str,
        request: JMeterTestRequest,
        worker: JMeterWorkerInfo,
    ) -> None:
        """Execute *operation_id* on the already-assigned *worker*.

        Called by the Dispatcher only after the pool assigned *worker* — this
        method does no scheduling of its own. It never raises: every outcome is
        recorded as a lifecycle transition, and the worker is released on every
        path, including the ones nobody planned for.
        """
        started_at = datetime.now(timezone.utc)
        try:
            self._operation_service.start(operation_id)

            jmx_plan = self._generate_plan(operation_id, request)
            self._validate_plan(jmx_plan, request)

            workspace = self._execute_on_worker(operation_id, request, jmx_plan, worker)
            summary, metrics = self._parse_results(operation_id, request, workspace)

            self._persist_result(
                operation_id=operation_id,
                request=request,
                worker=worker,
                workspace=workspace,
                started_at=started_at,
                summary=summary,
                metrics=metrics,
            )
        except JMeterPlanValidationError as exc:
            # Refusing to run is the correct outcome, not an internal error.
            self._logger.warning(
                "Operation '%s' rejected by plan validation: %s", operation_id, exc
            )
            self._operation_service.fail(operation_id, str(exc))
        except Exception as exc:  # pylint: disable=broad-except
            # exc_info keeps the traceback: the persisted error is str(exc),
            # which names *what* went wrong, and this is the only record of
            # *where*. Without it an unexpected failure in any of five phases
            # reads identically in the log.
            self._logger.error(
                "Operation '%s' failed after %.1fs: %s",
                operation_id,
                (datetime.now(timezone.utc) - started_at).total_seconds(),
                exc,
                exc_info=True,
            )
            self._operation_service.fail(operation_id, str(exc))
        finally:
            self._pool_manager.release_worker(worker.worker_id, operation_id)

    # ── Pipeline phases ──────────────────────────────────────────────────

    def _generate_plan(self, operation_id: str, request: JMeterTestRequest) -> str:
        """Phase 1 — build a JMX plan, unless the caller supplied one.

        A supplied plan is passed straight through to validation. It is *not*
        merged with the generated one and the load-profile fields are not
        applied to it: silently editing someone's test plan would make the thing
        that runs differ from the thing they wrote and reviewed.

        Raises:
            JMeterPlanValidationError: A plan was supplied but the server does
                not accept supplied plans.
        """
        if request.jmx_plan is None:
            return self._generator.generate(operation_id, request)

        if not self._config.jmeter_allow_supplied_plan:
            raise JMeterPlanValidationError(
                "This server does not accept caller-supplied JMeter test plans. "
                "Omit jmx_plan and the plan will be generated from the load "
                "profile instead."
            )

        self._logger.info(
            "Operation '%s' supplied its own test plan — generation skipped",
            operation_id,
        )
        return request.jmx_plan

    def _validate_plan(self, jmx_plan: str, request: JMeterTestRequest) -> None:
        """Phase 2 — the single G4 enforcement point, before anything executes.

        Runs for generated and supplied plans alike, and before a worker is
        engaged, so an unauthorized target never generates traffic.
        """
        self._validator.validate(jmx_plan, request)

    def _execute_on_worker(
        self,
        operation_id: str,
        request: JMeterTestRequest,
        jmx_plan: str,
        worker: JMeterWorkerInfo,
    ) -> str:
        """Phase 3 — hand the plan to *worker*, wait, return its workspace.

        Owns none of the subprocess mechanics; that is the Worker's. What it
        owns is the waiting: polling the worker's reported state and translating
        it into operation progress until the run reaches a terminal state.

        Raises:
            RuntimeError: The worker declined, lost the run, or reported failure.
        """
        # The server's own record of the hand-off. It deliberately overlaps the
        # worker's pre-launch line: the two are written by different processes
        # into different log files, and when a worker container is gone — the
        # common case for a failure worth investigating — this is the only side
        # left. operation_id is the key that joins them.
        self._logger.info(
            "Dispatching JMeter execution to worker",
            extra={
                "event": "jmeter_dispatch",
                "operation_id": operation_id,
                "worker_id": worker.worker_id,
                "target_url": LoggerUtils.redact_url_credentials(request.target_url),
                "execution_mode": (
                    "uploaded_jmx" if request.jmx_plan is not None else "generated_jmx"
                ),
                "thread_count": request.thread_count,
                "ramp_up_seconds": request.ramp_up_seconds,
                "hold_seconds": request.hold_seconds,
            },
        )

        client = self._pool_manager.get_client(worker)
        ack = client.send_assignment(
            JMeterAssignmentRequest(
                operation_id=operation_id, request=request, jmx_plan=jmx_plan
            )
        )
        if not ack.accepted:
            raise RuntimeError(
                f"Worker '{worker.worker_id}' declined the assignment: "
                f"{ack.message or 'no reason given'}"
            )

        self._operation_service.report_progress(
            operation_id,
            stage="running",
            progress=10,
            worker_id=worker.worker_id,
        )
        return self._await_completion(operation_id, request, worker, client)

    def _await_completion(self, operation_id, request, worker, client) -> str:
        """Poll the worker until its run reaches a terminal state."""
        poll_interval = self._config.jmeter_execution_poll_interval_seconds
        # The worker enforces the real timeout; this is the Runtime's own
        # backstop for a worker that accepts a run and then goes quiet without
        # its heartbeat expiring. Generous on purpose — it must never fire
        # before the worker's own limit.
        deadline = time.monotonic() + min(
            request.ramp_up_seconds + request.hold_seconds + 900,
            self._config.jmeter_max_run_seconds + 900,
        )

        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                report = client.fetch_execution_state(operation_id)
            except JMeterWorkerCommunicationError as exc:
                # A blip while a load test runs is expected — the worker is
                # saturated by design. Keep polling; the heartbeat sweep is what
                # decides a worker is actually gone.
                self._logger.debug(
                    "Could not read state for '%s' (retrying): %s", operation_id, exc
                )
                continue

            if report.state == JMeterExecutionState.SUCCEEDED:
                if not report.workspace:
                    raise RuntimeError(
                        "The worker reported success but named no workspace, so "
                        "the run produced nothing that can be read back."
                    )
                return report.workspace
            if report.state == JMeterExecutionState.FAILED:
                raise RuntimeError(report.message or "The JMeter run failed.")
            if report.state == JMeterExecutionState.UNKNOWN:
                raise RuntimeError(
                    f"Worker '{worker.worker_id}' has no record of this run — it "
                    "most likely restarted mid-execution."
                )

        raise RuntimeError(
            f"Worker '{worker.worker_id}' did not report a terminal state before "
            "the runtime deadline."
        )

    def _parse_results(
        self, operation_id: str, request: JMeterTestRequest, workspace: str
    ) -> Tuple[Optional[object], Optional[object]]:
        """Phase 4 — turn the JTL into numbers.

        The worker and the server share the report volume (docker-compose.yml),
        so the results file the worker wrote is readable here by path; there is
        no fetch step.

        **The only pipeline phase whose failure does not fail the operation.**
        Everything before it decides *whether the test may run*; this one reads
        back what a run that already happened produced. Losing the numbers is a
        degraded result, not a failed test — the target really was loaded, and
        the HTML dashboard and raw JTL are still linked from the artifacts. The
        contract makes both sections optional precisely so this case has a
        truthful representation instead of a fabricated one.
        """
        jtl_path = f"{workspace.rstrip('/')}/{JMeterProcessRunner.RESULTS_FILE}"
        try:
            return self._parser.parse(jtl_path, request.target_url)
        except JMeterResultsFileError as exc:
            self._logger.error(
                "Operation '%s' ran but its results file could not be read (%s) — "
                "persisting artifacts without metrics",
                operation_id,
                exc,
            )
            return None, None

    def _persist_result(
        self,
        *,
        operation_id: str,
        request: JMeterTestRequest,
        worker: JMeterWorkerInfo,
        workspace: str,
        started_at: datetime,
        summary,
        metrics,
    ) -> None:
        """Phase 5 — assemble the result contract and complete the operation.

        No ``findings=`` is passed to ``complete()``: a load test has no
        severity breakdown (ADR-0010), and the notification layer already
        handles its absence.

        The run package's ``execution.json`` and ``summary.html`` are written
        here, from the same values that go into the result, because this is the
        only point at which the request, the worker and both timestamps are all
        in hand. It happens *before* ``complete()`` so an operation is never
        reported finished while its package is still half-written.
        """
        finished_at = datetime.now(timezone.utc)
        # Best-effort, and guarded *here* rather than trusting the writer to
        # catch everything: this method decides whether the operation completes,
        # so this is where the guarantee has to hold. The package is a
        # convenience layered on a run that already succeeded — no failure in it
        # may discard a real load test whose measurements are already safe.
        try:
            self._package_writer.write(
                workspace=workspace,
                operation_id=operation_id,
                request=request,
                worker=worker,
                started_at=started_at,
                finished_at=finished_at,
                summary=summary,
                metrics=metrics,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.error(
                "Could not write the run package for operation '%s' (the run "
                "itself is unaffected): %s",
                operation_id,
                exc,
            )

        result = JMeterTestResult(
            operation_id=operation_id,
            summary=summary,
            metrics=metrics,
            artifacts=self._artifacts_for(workspace),
            metadata=JMeterExecutionMetadata(
                worker_id=worker.worker_id,
                started_at=started_at,
                finished_at=finished_at,
                thread_count=request.thread_count,
                ramp_up_seconds=request.ramp_up_seconds,
                hold_seconds=request.hold_seconds,
            ),
        )
        self._operation_service.complete(
            operation_id, result=result.model_dump(mode="json", by_alias=True)
        )
        # Wall-clock for the whole pipeline, which is not the same number as
        # either the declared load window or the parser's measured window — it
        # includes plan generation, JVM startup and dashboard generation. The
        # gap between it and the declared duration is what says whether a run
        # is spending its time generating load or waiting on overhead.
        self._logger.info(
            "Operation '%s' completed in %.1fs (worker '%s')",
            operation_id,
            (finished_at - started_at).total_seconds(),
            worker.worker_id,
        )

    # ── Artifacts ────────────────────────────────────────────────────────

    def _artifacts_for(self, workspace: str) -> JMeterArtifacts:
        """Map a run workspace onto the public URLs its files are served at.

        The worker writes into the same ``./public/report/jmeter/detail-report``
        tree the server serves from, so there is no upload step — see
        docker-compose.yml.

        **These are absolute URLs, not server-relative paths.** Everything that
        consumes them — an MCP client, a Slack card, a browser — receives the
        result detached from the HTTP request that produced it, and so has
        nothing to resolve a leading ``/`` against. The host comes from
        ``mcp_server_public_url``, which is exactly what
        ``OwaspZapHelpers.save_reports`` uses for ZAP's report links; both tools
        therefore answer in the same form.

        ponytail: shared-volume artifact handoff, which ties worker and server
        to one filesystem. If workers ever run off-host, replace this with an
        artifact upload from the worker; the result contract does not change.
        """
        if "/public" not in workspace:
            # Workspace outside the served tree (a test, or a misconfigured
            # deployment): record no links rather than fabricate broken ones.
            return JMeterArtifacts()

        served_path = "/public" + workspace.split("/public", 1)[-1]
        base = self._config.mcp_server_public_url.rstrip("/") + served_path
        return JMeterArtifacts(
            summary_page=f"{base}/{SUMMARY_PAGE_FILE}",
            view_report=f"{base}/{JMeterProcessRunner.REPORT_DIR}/index.html",
            results_data=f"{base}/{JMeterProcessRunner.RESULTS_FILE}",
            test_plan=f"{base}/{JMeterProcessRunner.PLAN_FILE}",
            engine_log=f"{base}/{JMeterProcessRunner.ENGINE_LOG}",
        )

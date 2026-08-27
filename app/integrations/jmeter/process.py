"""The JMeter subprocess adapter.

Owns exactly one thing: turning a JMX file into a finished JMeter process and
the files it wrote. Launch, timeout, cancellation, cleanup, exit code — and
nothing above that. It does not read the JTL it produced, does not know what an
Operation is, and never touches the database.

The command is always JMeter's non-GUI form::

    jmeter -n -t <test-plan.jmx> -l <test-results.jtl> -e -o <html-report/> -j <engine.log>

Which JMeter that resolves to is the worker image's business, not a setting —
see ``docker/jmeter/Dockerfile``.

"The files it wrote" is checked, not assumed: a cleanly exited run has its
workspace verified against :attr:`JMeterProcessRunner.EXPECTED_ARTIFACTS` and
reports by name anything absent. That is still the same one thing — this class
knows those filenames because it chose them — and it stops there: it reports
the gap, and :class:`~app.integrations.jmeter.worker.JMeterWorker` decides what
the gap means.
"""

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.core.mcp_logger import MCPLogger

#: How much of a failed run's stdout/stderr is carried into the diagnostics
#: log. The tail, not the head: JMeter's fatal message is the last thing it
#: writes, while the head is the startup banner. Bounded because a long run's
#: summariser output is unbounded and a log line is not the place for it.
_OUTPUT_TAIL_CHARS = 4000


class JMeterProcessError(RuntimeError):
    """JMeter could not be started at all (missing binary, unusable workspace)."""


@dataclass(frozen=True)
class JMeterRunOutcome:
    """What one JMeter process did.

    ``exit_code`` is JMeter's own: 0 is a completed run. A non-zero code means
    the engine itself failed — note that individual failed *samples* do not
    produce a non-zero exit code, so this says nothing about whether the target
    behaved well. That judgment needs the JTL.

    ``missing_artifacts`` names the expected files a *cleanly exited* run did
    not leave behind (empty for every other ending, where absence is expected
    rather than wrong). It is deliberately **not** folded into
    :attr:`succeeded`: that property answers "how did the process end", which
    is the mechanic this class owns, while "was the run usable" is an
    interpretation and belongs to :class:`~app.integrations.jmeter.worker.JMeterWorker`.
    Keeping the two apart is what stops this adapter from making a lifecycle
    judgment.
    """

    exit_code: int
    timed_out: bool
    cancelled: bool
    workspace: str
    missing_artifacts: Tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


class JMeterProcessRunner:
    """Runs JMeter as a subprocess, one run at a time per instance."""

    #: Fixed filenames inside a run workspace. The Runtime builds public URLs
    #: from these, so they are part of the contract between the two and are
    #: deliberately not configurable.
    #:
    #: The names describe the *content*, not the tool, because a run package is
    #: downloaded, attached to tickets and archived — read months later, by
    #: someone who did not run it. "results.jtl" and "report/" mean nothing out
    #: of context; "test-results.jtl" and "html-report/" survive the trip.
    #: These are physical names only: the ``JMeterArtifacts`` JSON keys they are
    #: reported under are unchanged.
    PLAN_FILE = "test-plan.jmx"
    RESULTS_FILE = "test-results.jtl"
    REPORT_DIR = "html-report"
    ENGINE_LOG = "engine.log"

    #: What a cleanly exited run must have left in its workspace, checked
    #: rather than assumed. JMeter can exit 0 having written no dashboard (a
    #: JTL with zero samples is the common way), and every consumer downstream
    #: — the parser, the artifact URLs, the run package — reads these paths by
    #: name. Discovering the gap here names the missing file; discovering it
    #: later surfaces as an unreadable JTL or a report link that 404s.
    EXPECTED_ARTIFACTS = (
        PLAN_FILE,
        RESULTS_FILE,
        ENGINE_LOG,
        f"{REPORT_DIR}/index.html",
    )

    def __init__(
        self,
        binary: str = "jmeter",
        terminate_grace_seconds: float = 15.0,
        logger: Optional[MCPLogger] = None,
    ) -> None:
        """
        Args:
            binary: JMeter executable, resolved on PATH. Not a version selector.
            terminate_grace_seconds: How long a terminated run gets to flush its
                JTL after SIGTERM before it is killed.
            logger: Optional logger; one is created if omitted.
        """
        self._binary = binary
        self._grace = terminate_grace_seconds
        self._logger = logger or MCPLogger("JMeterProcessRunner")
        self._processes: Dict[str, subprocess.Popen] = {}
        self._cancelled: set = set()
        self._lock = threading.Lock()

    # ── Execution ────────────────────────────────────────────────────────

    def run(
        self,
        operation_id: str,
        workspace: str,
        jmx_xml: str,
        timeout_seconds: float,
    ) -> JMeterRunOutcome:
        """Write *jmx_xml* into *workspace* and run JMeter over it to completion.

        Blocks until the process ends, times out, or is cancelled. On timeout or
        cancellation the process is asked to stop first (SIGTERM) and only then
        killed, so JMeter can flush the results it has already collected —
        partial results from a 20-minute run are worth far more than a clean
        kill.

        Raises:
            JMeterProcessError: JMeter could not be started at all.
        """
        workspace_path = self._prepare_workspace(workspace, jmx_xml)
        command = self._build_command(workspace_path)
        # Quoted so the line can be pasted into a shell as-is to reproduce the
        # run. Safe to log verbatim: argv is fixed, built only from the binary
        # name and paths inside this run's workspace, and carries no
        # caller-supplied value at all — the target and any headers live inside
        # the JMX file, never on the command line (Guardrail G1).
        printable_command = shlex.join(command)

        self._logger.info(
            "Launching JMeter",
            extra={
                "event": "jmeter_launch",
                "operation_id": operation_id,
                "workspace": str(workspace_path),
                "timeout_seconds": f"{timeout_seconds:.0f}",
                "command": printable_command,
            },
        )

        # Captured to temporary files rather than PIPE: a run lasts minutes and
        # nothing reads the pipe while it does, so a PIPE would fill its buffer
        # and deadlock the very process it was meant to observe. DEVNULL — what
        # this used to do — is the other extreme: a JMeter that dies on startup
        # explains itself on stderr, and that explanation was being discarded.
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        try:
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    command,
                    cwd=str(workspace_path),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                raise JMeterProcessError(
                    f"Could not start JMeter ('{self._binary}'): {exc}"
                ) from exc

            with self._lock:
                self._processes[operation_id] = process

            started_at = datetime.now(timezone.utc)
            started_monotonic = time.monotonic()
            timed_out = False
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._logger.warning(
                    "JMeter for operation '%s' exceeded %.0fs — terminating",
                    operation_id,
                    timeout_seconds,
                )
                self._stop(process)
            finally:
                with self._lock:
                    self._processes.pop(operation_id, None)
                    cancelled = operation_id in self._cancelled
                    self._cancelled.discard(operation_id)

            elapsed = time.monotonic() - started_monotonic
            finished_at = datetime.now(timezone.utc)
            exit_code = process.returncode if process.returncode is not None else -1

            # Only a clean ending is expected to have produced a full artifact
            # set: a terminated or cancelled run legitimately has no HTML
            # dashboard, and reporting those files as "missing" would turn a
            # known outcome into a second, misleading one.
            missing = (
                self._missing_artifacts(workspace_path)
                if exit_code == 0 and not timed_out and not cancelled
                else ()
            )

            self._logger.info(
                "JMeter finished",
                extra={
                    "event": "jmeter_finished",
                    "operation_id": operation_id,
                    "started_at": started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "elapsed_seconds": f"{elapsed:.3f}",
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "cancelled": cancelled,
                    "missing_artifacts": ", ".join(missing) or "none",
                },
            )

            if exit_code != 0 or missing:
                self._log_failure_diagnostics(
                    operation_id=operation_id,
                    workspace=workspace_path,
                    command=printable_command,
                    exit_code=exit_code,
                    missing=missing,
                    stdout_file=stdout_file,
                    stderr_file=stderr_file,
                )

            return JMeterRunOutcome(
                exit_code=exit_code,
                timed_out=timed_out,
                cancelled=cancelled,
                workspace=str(workspace_path),
                missing_artifacts=missing,
            )
        finally:
            stdout_file.close()
            stderr_file.close()

    def cancel(self, operation_id: str) -> bool:
        """Stop the run for *operation_id*. Returns whether one was running.

        Safe to call for an operation that already finished or never started —
        cancellation racing with completion is normal, not an error.
        """
        with self._lock:
            process = self._processes.get(operation_id)
            if process is None:
                return False
            self._cancelled.add(operation_id)

        self._logger.info("Cancelling JMeter run for operation '%s'", operation_id)
        self._stop(process)
        return True

    # ── Verification and diagnostics ─────────────────────────────────────

    def _missing_artifacts(self, workspace: Path) -> Tuple[str, ...]:
        """Which of :attr:`EXPECTED_ARTIFACTS` *workspace* does not contain.

        Existence only. Whether a present file is *good* — a JTL with rows, a
        dashboard with data — is the parser's question, and answering it here
        would put a second opinion about the results in the process adapter.
        """
        missing = tuple(
            name for name in self.EXPECTED_ARTIFACTS if not (workspace / name).exists()
        )
        if missing:
            self._logger.error(
                "JMeter exited cleanly but its workspace is incomplete",
                extra={
                    "event": "jmeter_artifacts_missing",
                    "workspace": str(workspace),
                    # Named individually: "an artifact is missing" sends someone
                    # to look, "html-report/index.html is missing" tells them
                    # dashboard generation is what failed.
                    "missing_artifacts": ", ".join(missing),
                    "present_artifacts": ", ".join(
                        name for name in self.EXPECTED_ARTIFACTS if name not in missing
                    )
                    or "none",
                },
            )
        return missing

    def _log_failure_diagnostics(
        self,
        *,
        operation_id: str,
        workspace: Path,
        command: str,
        exit_code: int,
        missing: Tuple[str, ...],
        stdout_file,
        stderr_file,
    ) -> None:
        """Everything needed to diagnose a bad run, in one place, once.

        Emitted here rather than raised upward because the exception path is
        for *failures to start*; a run that started and ended badly is a normal
        return value carrying an exit code, and the detail behind that code
        would otherwise be gone the moment the temporary files close.
        """
        self._logger.error(
            "JMeter run did not complete cleanly",
            extra={
                "event": "jmeter_run_failed",
                "operation_id": operation_id,
                "exit_code": exit_code,
                "workspace": str(workspace),
                "command": command,
                "missing_artifacts": ", ".join(missing) or "none",
                "stdout_tail": self._tail(stdout_file) or "(empty)",
                "stderr_tail": self._tail(stderr_file) or "(empty)",
            },
        )

    @staticmethod
    def _tail(stream) -> str:
        """The last :data:`_OUTPUT_TAIL_CHARS` bytes *stream* holds, as text.

        Never raises: this runs on the failure path, and a diagnostics helper
        that can itself fail would replace the error being reported with its
        own.
        """
        try:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _OUTPUT_TAIL_CHARS))
            return stream.read().decode("utf-8", errors="replace").strip()
        except (OSError, ValueError):  # pragma: no cover - closed or unseekable
            return ""

    # ── Internals ────────────────────────────────────────────────────────

    def _stop(self, process: subprocess.Popen) -> None:
        """SIGTERM, wait out the grace period, then SIGKILL.

        Signals the whole process group: JMeter's launcher spawns a JVM child,
        and terminating only the launcher would leave the JVM generating load
        against the target — the worst possible orphan for a load engine.
        """
        for send_signal, label in ((signal.SIGTERM, "SIGTERM"), (signal.SIGKILL, "SIGKILL")):
            if process.poll() is not None:
                return
            try:
                self._signal_group(process, send_signal)
            except OSError as exc:  # pragma: no cover - process already reaped
                self._logger.debug("Could not send %s: %s", label, exc)
                return
            try:
                process.wait(timeout=self._grace)
                return
            except subprocess.TimeoutExpired:
                self._logger.warning(
                    "JMeter did not exit after %s — escalating", label
                )

    @staticmethod
    def _signal_group(process: subprocess.Popen, send_signal: int) -> None:
        """Signal the process group started by ``start_new_session=True``."""
        os.killpg(os.getpgid(process.pid), send_signal)

    def _prepare_workspace(self, workspace: str, jmx_xml: str) -> Path:
        """Create a clean workspace containing the plan to execute.

        The path is resolved to an absolute one **before** anything else uses
        it, and that is load-bearing rather than tidiness: :meth:`run` launches
        JMeter with ``cwd`` set to this directory while passing the same path in
        argv. A relative workspace — which is what ``jmeter_workspace_dir``
        ("./public/report/jmeter/detail-report") produces — would then be
        resolved a second time against that cwd, so JMeter looks for
        ``<workspace>/<workspace>/plan.jmx`` and exits 1 before running a single
        sample. Resolving once here fixes it for every argument at the source.
        """
        path = Path(workspace).resolve()
        try:
            # JMeter refuses to write its HTML dashboard into a non-empty
            # directory, so a re-run of the same operation starts clean.
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
            (path / self.PLAN_FILE).write_text(jmx_xml, encoding="utf-8")
        except OSError as exc:
            raise JMeterProcessError(
                f"Could not prepare the JMeter workspace at '{workspace}': {exc}"
            ) from exc
        return path

    def _build_command(self, workspace: Path) -> list:
        return [
            self._binary,
            "-n",
            "-t",
            str(workspace / self.PLAN_FILE),
            "-l",
            str(workspace / self.RESULTS_FILE),
            "-e",
            "-o",
            str(workspace / self.REPORT_DIR),
            "-j",
            str(workspace / self.ENGINE_LOG),
        ]

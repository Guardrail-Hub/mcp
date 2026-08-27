"""Transport between the server and one JMeter worker agent.

The server pushes assignments to the worker and asks it for state; the worker
never pushes back. That direction is a deliberate consequence of the accepted
boundaries: a worker-initiated lifecycle callback would make the worker a
second writer of operation state, and ``JMeterRuntime`` is the sole owner of
lifecycle persistence for JMeter operations
(``architecture/jmeter-engine/runtime-responsibility.md`` §3). Keeping the
worker answer-only means the Runtime stays the one place that decides what a
worker's report means for the Operation.

Mirrors how the ZAP pool talks to a ZAP worker over that worker's own HTTP API
— same direction, same per-worker client caching in the Pool Manager — without
importing any ZAP module.

This client is transport only: it sends, receives, and validates the message
shapes. It starts nothing, persists nothing, and interprets nothing.
"""

from contextlib import nullcontext
from typing import Optional

import httpx

from app.core.mcp_logger import MCPLogger
from app.schemas.tools.jmeter.worker_assignment import (
    JMeterAssignmentAck,
    JMeterAssignmentRequest,
    JMeterExecutionState,
    JMeterExecutionStateReport,
)


class JMeterWorkerCommunicationError(RuntimeError):
    """The worker could not be reached, or answered in a shape we don't accept."""


class JMeterWorkerClient:
    """HTTP client bound to exactly one JMeter worker's agent endpoint."""

    def __init__(
        self,
        endpoint: str,
        worker_id: str,
        timeout_seconds: float = 30.0,
        transport: Optional[httpx.Client] = None,
    ) -> None:
        """
        Args:
            endpoint: Base URL the worker reported at registration.
            worker_id: The worker this client speaks to; used in messages and
                logs so a failure names the machine.
            timeout_seconds: Per-request transport timeout. Bounds a hung
                worker; it is not a run-duration limit — a load test runs far
                longer than any single request here.
            transport: Injectable ``httpx.Client`` so this class is testable
                without a live worker.
        """
        self.endpoint = endpoint.rstrip("/")
        self.worker_id = worker_id
        self._timeout = timeout_seconds
        self._transport = transport
        self._logger = MCPLogger("JMeterWorkerClient")

    # ── Messages ─────────────────────────────────────────────────────────

    def send_assignment(self, assignment: JMeterAssignmentRequest) -> JMeterAssignmentAck:
        """Ask the worker to take *assignment*; return its acknowledgement.

        A worker that declines (already busy, draining) returns
        ``accepted=False``. That is a normal answer, not an error — the caller
        releases the worker and lets the operation stay queued.

        Raises:
            JMeterWorkerCommunicationError: The worker was unreachable, replied
                with an error status, or sent a body that is not an ack.
        """
        payload = self._post(
            "/assignments", assignment.model_dump(mode="json", by_alias=True)
        )
        try:
            return JMeterAssignmentAck.model_validate(payload)
        except Exception as exc:
            raise JMeterWorkerCommunicationError(
                f"Worker '{self.worker_id}' returned an unreadable assignment ack: {exc}"
            ) from exc

    def fetch_execution_state(self, operation_id: str) -> JMeterExecutionStateReport:
        """Ask the worker where its run of *operation_id* currently is.

        The Runtime polls this during its Execute phase. An operation the worker
        has never heard of comes back as
        :attr:`JMeterExecutionState.UNKNOWN` rather than raising, because "the
        worker restarted and lost the run" is a state the Runtime must handle,
        not a transport failure.

        Raises:
            JMeterWorkerCommunicationError: The worker was unreachable, replied
                with an error status, or sent a body that is not a state report.
        """
        payload = self._get(f"/assignments/{operation_id}")
        if payload is None:
            return JMeterExecutionStateReport(
                operation_id=operation_id,
                worker_id=self.worker_id,
                state=JMeterExecutionState.UNKNOWN,
                message=f"Worker '{self.worker_id}' has no record of this operation.",
            )
        try:
            return JMeterExecutionStateReport.model_validate(payload)
        except Exception as exc:
            raise JMeterWorkerCommunicationError(
                f"Worker '{self.worker_id}' returned an unreadable state report: {exc}"
            ) from exc

    # ── Transport ────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        try:
            with self._client_context() as client:
                response = client.post(f"{self.endpoint}{path}", json=body)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise JMeterWorkerCommunicationError(
                f"POST {path} to JMeter worker '{self.worker_id}' "
                f"({self.endpoint}) failed: {exc}"
            ) from exc

    def _get(self, path: str) -> Optional[dict]:
        """GET *path*; ``None`` when the worker reports 404 (nothing to report)."""
        try:
            with self._client_context() as client:
                response = client.get(f"{self.endpoint}{path}")
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise JMeterWorkerCommunicationError(
                f"GET {path} from JMeter worker '{self.worker_id}' "
                f"({self.endpoint}) failed: {exc}"
            ) from exc

    def _client_context(self):
        """The injected transport, or a short-lived one bound to the timeout.

        An injected transport is wrapped in ``nullcontext`` so leaving the
        ``with`` block does not close a client its owner still needs.
        """
        if self._transport is not None:
            return nullcontext(self._transport)
        return httpx.Client(timeout=self._timeout)

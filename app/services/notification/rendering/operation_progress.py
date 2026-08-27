"""Generic progress model for the notification context.

The single, vocabulary-neutral shape the notification layer understands. It does
NOT know about OWASP ZAP, scans, or any tool concept: each tool maps its own
internal progress into this common model, and the renderer/notifier work only
against this model. That decoupling is what lets a new tool gain progress
notifications without touching the notification layer (and vice versa), and it
keeps the model transport-independent — the same snapshot is renderable by
Slack, Discord, Teams, Email, a web dashboard, or a CLI.

Design notes:
- The lifecycle phase and the severity breakdown are **owned by the domain
  layer** (``app.domain.lifecycle.OperationPhase`` and
  ``app.domain.findings.FindingSummary``, Decision 0008). This module previously
  defined its own copies of both to avoid importing a tool schema; that
  duplication is resolved — it now consumes the canonical types.
- ``OperationProgress`` carries the lifecycle/progress fields plus optional
  *presentation* context grouped by the product's information hierarchy:
    L1 (identity/what/where): title, target, phase.
    L2 (activity): stage, progress, elapsed.
    L3 (outcome): findings, report_link, next_action, duration, summary, reason.
    L4 (infrastructure, Debug Mode only): reference/operation_id + the free-form
        ``debug`` map (worker, container, scheduler, ...).
  Every presentation field is optional and defaults to ``None`` so a caller only
  supplies what it has, and the renderer simply omits what is absent.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional

from app.domain.findings import FindingSummary
from app.domain.lifecycle import OperationPhase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class OperationProgress:
    """A generic, platform-independent snapshot of one operation's progress.

    Attributes:
        operation_id: Stable id of the operation (Level 4 / Debug only in the UI).
        phase: The lifecycle phase this snapshot represents.
        stage: Optional human-readable stage within the phase (e.g.
            ``"active scan"``). Tool-specific text the renderer humanises.
        progress: Optional completion percentage in ``[0, 100]``. ``None`` means
            "progress not available" (the platform then shows no percentage).
        message: Optional free-text detail for this snapshot.
        timestamp: When this snapshot was produced (UTC).
        title: Friendly operation title (Level 1), e.g. ``"API Security Scan"``.
        target: What is being scanned (Level 1), e.g. ``"POST /api/auth/login"``.
        reference: Short, non-sensitive identifier for humans (e.g. ``"1D015005"``)
            shown in place of the full ``operation_id``.
        worker_id: Optional id of the worker (Level 4 / Debug only).
        elapsed_seconds: Optional elapsed time since RUNNING started (Level 2).
        duration_seconds: Optional total run duration (Level 3, COMPLETED).
        findings: Optional severity breakdown (Level 3, COMPLETED).
        summary: Optional short result summary (Level 3, COMPLETED).
        report_link: Optional link to the full report (Level 3, COMPLETED).
        next_action: Optional recommended next action (Level 3).
        reason: Optional failure reason (Level 3, FAILED / CANCELLED).
        failed_phase: Optional phase the operation failed in (Level 3, FAILED).
        debug: Optional free-form infrastructure metadata (Level 4). Rendered
            only when the transport/renderer is in Debug Mode.
    """

    operation_id: str
    phase: OperationPhase
    stage: Optional[str] = None
    progress: Optional[int] = None
    message: Optional[str] = None
    timestamp: datetime = field(default_factory=_utcnow)
    title: Optional[str] = None
    target: Optional[str] = None
    reference: Optional[str] = None
    worker_id: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    duration_seconds: Optional[float] = None
    findings: Optional[FindingSummary] = None
    summary: Optional[str] = None
    report_link: Optional[str] = None
    next_action: Optional[str] = None
    reason: Optional[str] = None
    failed_phase: Optional[str] = None
    debug: Optional[Mapping[str, str]] = None

    def __post_init__(self) -> None:
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise ValueError("progress must be within [0, 100]")

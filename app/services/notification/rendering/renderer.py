"""Render a generic OperationProgress into a professional status card.

Turns the vocabulary-neutral :class:`OperationProgress` into the plain-text body
a channel adapter delivers. It knows nothing about any tool — only the generic
model — and it presents the operation the way a product would, not the way a log
would. Every card answers, top to bottom: *what* operation, *where* (target),
*what's happening now*, then *what to care about*, then *what to do next*.

Presentation hierarchy (most important first):
    L1  title (header) · target · status
    L2  current phase · progress · elapsed
    L3  findings · report · recommended next action · duration · reason
    L4  operation id · worker · container · scheduler  (Debug Mode only)

Infrastructure identifiers (L4) are hidden unless the renderer is constructed
with ``debug=True``; the default card shows only a short, non-sensitive
reference (``Operation #1D015005``). The output is intentionally the safe
common denominator every transport can deliver — a Slack/Discord/Teams adapter
is free to re-render the same model natively (blocks, embeds, cards); the domain
model stays transport-agnostic.
"""

from typing import List, Optional, Tuple

from app.core.ports.notification_channel import NotificationContent, NotificationField
from app.domain.findings import FindingSummary, Severity
from app.domain.lifecycle import OperationPhase
from app.services.notification.rendering.operation_progress import (
    OperationProgress,
)

_DIVIDER = "━" * 16

# Phase → header icon. Communicates state at a glance before any text is read.
_PHASE_ICON = {
    OperationPhase.QUEUED: "🕒",
    OperationPhase.RUNNING: "🔎",
    OperationPhase.COMPLETED: "✅",
    OperationPhase.FAILED: "❌",
    OperationPhase.CANCELLED: "🚫",
}

# Phase → the L1 status line (user-oriented, not technical).
_PHASE_STATUS = {
    OperationPhase.QUEUED: "Waiting for available worker",
    OperationPhase.COMPLETED: "Completed",
    OperationPhase.FAILED: "Failed",
    OperationPhase.CANCELLED: "Cancelled",
}

# Technical stage wording → user-friendly phrasing. Keys are compared
# case-insensitively so a tool can emit "Active Scan" or "active scan".
_STAGE_WORDING = {
    "queued": "Waiting for available worker",
    "initializing": "Preparing scan",
    "preparing scan": "Preparing scan",
    "spider": "Discovering attack surface",
    "spidering": "Discovering attack surface",
    "passive scan": "Passive analysis",
    "passive": "Passive analysis",
    "active scan": "Security testing",
    "active": "Security testing",
    "report generation": "Preparing report",
    "generating report": "Preparing report",
    "completed": "Scan completed",
    "failed": "Scan failed",
}

_DEFAULT_TITLE = "Security Scan"


def humanize_stage(stage: Optional[str]) -> Optional[str]:
    """Return the user-friendly phrasing for *stage* (or the stage unchanged)."""
    if not stage:
        return None
    return _STAGE_WORDING.get(stage.strip().lower(), stage)


class OperationNotificationRenderer:
    """Render an :class:`OperationProgress` snapshot to a status-card message.

    Args:
        debug: When true, appends a Debug section exposing Level-4 infrastructure
            identifiers (operation id, worker, container, ...). Default false, so
            the standard user experience never surfaces infrastructure detail.
    """

    def __init__(self, debug: bool = False) -> None:
        self._debug = debug

    def render_content(self, progress: OperationProgress) -> NotificationContent:
        """Return the *structured* content for *progress*.

        The single source of the card's information. A transport that can render
        natively (Slack Block Kit, a Discord embed) builds from this; the
        plain-text card produced by :meth:`render` is the same information
        flattened for transports that cannot. Neither is derived from the other —
        rendering is one-way, and no adapter recovers structure from text.
        """
        icon = _PHASE_ICON.get(progress.phase, "🛡️")
        fields = tuple(
            NotificationField(label=label, value=value)
            for label, value in self._fields_for(progress)
        )

        identifier = None
        identifier_help = None
        reference = None
        if progress.phase is OperationPhase.COMPLETED:
            identifier = progress.operation_id or progress.reference
            if identifier:
                identifier_help = (
                    "Use this Operation ID with get-result to retrieve the "
                    "complete scan report."
                )
        else:
            reference = progress.reference or self._fallback_reference(
                progress.operation_id
            )

        debug_fields: Tuple[NotificationField, ...] = ()
        if self._debug:
            debug_fields = tuple(
                NotificationField(label=label, value=value)
                for label, value in self._debug_entries(progress)
            )

        return NotificationContent(
            title=f"{icon} {progress.title or _DEFAULT_TITLE}",
            phase=progress.phase,
            fields=fields,
            target=progress.target,
            duration_text=(
                self._duration(progress.duration_seconds)
                if progress.duration_seconds is not None
                else None
            ),
            findings=progress.findings,
            report_link=progress.report_link,
            identifier=identifier,
            identifier_help=identifier_help,
            reference=reference,
            debug_fields=debug_fields,
        )

    def render(self, progress: OperationProgress) -> str:
        """Return the status-card body for *progress* according to its phase."""
        icon = _PHASE_ICON.get(progress.phase, "🛡️")
        title = progress.title or _DEFAULT_TITLE

        lines: List[str] = [f"{icon} {title}"]

        # Blank line after the title, and again before the identifier, so the
        # card reads as three scannable groups (title / details / how to act)
        # rather than one flat list — the visual hierarchy the UX asks for.
        fields = self._fields_for(progress)
        if fields:
            lines.append("")
            lines += self._format_fields(fields)

        # Identifier group. On completion the FULL operation id is shown (users
        # copy it into get-result to fetch the report). Everywhere else only a
        # short, non-sensitive reference is shown — the full id is Level-4
        # infrastructure and stays in Debug Mode.
        if progress.phase is OperationPhase.COMPLETED:
            identifier = progress.operation_id or progress.reference
            if identifier:
                lines.append("")
                lines.append(f"Operation ID: {identifier}")
                lines.append(
                    "Use this Operation ID with get-result to retrieve the "
                    "complete scan report."
                )
        else:
            reference = progress.reference or self._fallback_reference(progress.operation_id)
            if reference:
                lines.append("")
                lines.append(f"Operation #{reference}")

        debug_lines = self._debug_section(progress) if self._debug else []
        if debug_lines:
            lines.append(_DIVIDER)
            lines += debug_lines

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Per-phase field selection (the presentation hierarchy)
    # ------------------------------------------------------------------

    def _fields_for(self, p: OperationProgress) -> List[Tuple[str, str]]:
        # L1 — always: where (target) + what's happening (status).
        fields: List[Tuple[str, str]] = []
        if p.target:
            fields.append(("Target", p.target))
        fields.append(("Status", self._status_line(p)))

        if p.phase is OperationPhase.RUNNING:
            self._running_fields(p, fields)
        elif p.phase is OperationPhase.COMPLETED:
            self._completed_fields(p, fields)
        elif p.phase in (OperationPhase.FAILED, OperationPhase.CANCELLED):
            self._failed_fields(p, fields)
        return fields

    def _running_fields(self, p: OperationProgress, fields: List[Tuple[str, str]]) -> None:
        # L2 — activity: progress + elapsed (phase is already the status line).
        if p.progress is not None:
            fields.append(("Progress", self._bar(p.progress)))
        if p.elapsed_seconds is not None:
            fields.append(("Elapsed", self._duration(p.elapsed_seconds)))

    def _completed_fields(self, p: OperationProgress, fields: List[Tuple[str, str]]) -> None:
        # L3 — outcome: duration, severity results, and a direct report link.
        if p.duration_seconds is not None:
            fields.append(("Duration", self._duration(p.duration_seconds)))
        # Results only when statistics exist — omit gracefully, no placeholders.
        if p.findings is not None:
            fields.append(("Results", self._findings(p.findings)))
        elif p.summary:
            fields.append(("Results", p.summary))
        # Direct link to the full report (transports render it natively, e.g.
        # a "View Report" button on Slack). Omitted when no report is available.
        if p.report_link:
            fields.append(("Report", p.report_link))

    def _failed_fields(self, p: OperationProgress, fields: List[Tuple[str, str]]) -> None:
        # L3 — a single, human-readable reason (never a stack trace). Kept
        # minimal: the user needs why it failed and the Operation ID, nothing more.
        fields.append(("Reason", self._short_reason(p.reason or p.message)))

    # ------------------------------------------------------------------
    # Value builders
    # ------------------------------------------------------------------

    def _status_line(self, p: OperationProgress) -> str:
        if p.phase is OperationPhase.RUNNING:
            return humanize_stage(p.stage) or "Scanning in progress"
        return _PHASE_STATUS.get(p.phase, "In progress")

    @staticmethod
    def _findings(f: FindingSummary) -> str:
        if f.finding_count == 0:
            return "No vulnerabilities found"
        return (
            f"Critical {f.count_of(Severity.CRITICAL)} · "
            f"High {f.count_of(Severity.HIGH)} · "
            f"Medium {f.count_of(Severity.MEDIUM)} · "
            f"Low {f.count_of(Severity.LOW)} · "
            f"Info {f.count_of(Severity.INFORMATIONAL)}"
        )

    @staticmethod
    def _short_reason(reason: Optional[str]) -> str:
        """Keep failure text concise — one line, no stack traces."""
        text = (reason or "Unknown error").strip().splitlines()[0]
        return text if len(text) <= 160 else text[:157] + "..."

    # ------------------------------------------------------------------
    # Debug (Level 4) — only rendered when debug=True
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_reference(operation_id: Optional[str]) -> Optional[str]:
        """Derive a short, non-sensitive reference from an operation id.

        Drops any ``"<batch_type>:"`` prefix and keeps the first 8 alphanumeric
        characters, upper-cased (e.g. ``"api_scan:1d015005-..."`` -> ``"1D015005"``).
        Only used when the notifier did not already supply ``reference``.
        """
        if not operation_id:
            return None
        tail = operation_id.rsplit(":", 1)[-1]
        alnum = "".join(c for c in tail if c.isalnum())
        return (alnum[:8] or operation_id[:8]).upper()

    @staticmethod
    def _debug_entries(p: OperationProgress) -> List[Tuple[str, str]]:
        """Level-4 infrastructure entries. Shared by the text and structured paths."""
        entries: List[Tuple[str, str]] = [("Operation ID", p.operation_id)]
        if p.worker_id:
            entries.append(("Worker", p.worker_id))
        if p.debug:
            entries += [(k, str(v)) for k, v in p.debug.items()]
        return entries

    def _debug_section(self, p: OperationProgress) -> List[str]:
        # Level-4 infrastructure — only rendered in Debug Mode. The full
        # operation id lives here by default (it is only surfaced on the card
        # itself at completion, for get-result).
        return ["Debug", *self._format_fields(self._debug_entries(p))]

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_fields(fields: List[Tuple[str, str]]) -> List[str]:
        # "Label: value" per line — renders consistently across every transport
        # (Slack, Discord, Teams, email, CLI). Column alignment via padding was
        # dropped because it only looks right in a monospace font and reads like
        # a log; a labelled line reads like a product field.
        return [f"{label}: {value}" for label, value in fields]

    @staticmethod
    def _bar(progress: int, width: int = 10) -> str:
        clamped = max(0, min(100, progress))
        filled = round(clamped / 100 * width)
        return f"{'█' * filled}{'░' * (width - filled)} {clamped}%"

    @staticmethod
    def _duration(seconds: float) -> str:
        total = int(round(seconds))
        if total < 60:
            return f"{total}s"
        minutes, secs = divmod(total, 60)
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

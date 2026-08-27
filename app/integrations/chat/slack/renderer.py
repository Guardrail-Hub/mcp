"""Slack Block Kit rendering — structured content in, Slack blocks out.

The *only* place Slack-specific presentation exists. It renders
:class:`~app.core.ports.notification_channel.NotificationContent` — the
structured contract the port defines — directly into Block Kit.

Rendering is one-way. This module never receives, inspects, or parses a
rendered string to recover structure; it is given the information and formats
it. (The previous implementation regex-parsed the plain-text card back into
fields and severity counts, which silently broke whenever the application's
wording changed. That path is gone.)
"""

from typing import List, Optional

from app.core.ports.notification_channel import NotificationContent
from app.domain.findings import SEVERITY_ORDER, Severity
from app.domain.lifecycle import OperationPhase

# Product brand shown as a small context line at the top of rich cards.
BRAND = "🛡️ Guardrail Hub"

# Slack-specific presentation for each canonical severity. The severity
# vocabulary and its order come from the domain (Decision 0008); only the
# emoji — which is Slack presentation — lives here.
_SEVERITY_ICON = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🟢",
    Severity.INFORMATIONAL: "🔵",
}


class SlackBlockRenderer:
    """Formats structured notification content as Slack Block Kit."""

    def render(self, message: str, content: Optional[NotificationContent]) -> list:
        """Return Block Kit blocks for *content*, falling back to *message*.

        Args:
            message: The plain-text body. Used verbatim when no structured
                content is available — never parsed.
            content: The structured content to render natively.
        """
        if content is None:
            return self._plain_blocks(message)
        if content.phase is OperationPhase.COMPLETED and content.identifier:
            return self._completed_blocks(content)
        return self._compact_blocks(content)

    # ------------------------------------------------------------------
    # Layouts
    # ------------------------------------------------------------------

    @staticmethod
    def _plain_blocks(message: str) -> list:
        """Last-resort layout for a message with no structured content."""
        return [{"type": "section", "text": {"type": "mrkdwn", "text": message}}]

    def _compact_blocks(self, content: NotificationContent) -> list:
        """Compact layout: bold title, divider, then the labelled fields."""
        blocks: list = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{content.title}*"}}
        ]

        lines = [f"{f.label}: {f.value}" for f in content.fields]
        if content.identifier:
            # Monospace the operation id so it is easy to copy for get-result.
            lines.append(f"Operation ID: `{content.identifier}`")
            if content.identifier_help:
                lines.append(content.identifier_help)
        elif content.reference:
            lines.append(f"Operation #{content.reference}")

        if lines:
            blocks.append({"type": "divider"})
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}
            )

        blocks += self._debug_blocks(content)
        return blocks

    def _completed_blocks(self, content: NotificationContent) -> list:
        """The rich completed-scan product card.

        Brand context, a header stating completion, then one section per topic
        with dividers between them, a per-severity findings list, and a
        get-result footer.
        """
        blocks: list = [
            {"type": "context", "elements": [{"type": "mrkdwn", "text": BRAND}]},
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{content.title} Completed",
                    "emoji": True,
                },
            },
        ]

        if content.target:
            blocks += self._section(f"🎯 *Target*\n{content.target}")
        if content.findings is not None:
            blocks += self._section(self._findings_text(content))
        if content.duration_text:
            blocks += self._section(f"⏱ *Duration*\n{content.duration_text}")
        if content.report_link:
            # Native Slack link — renders as a clickable "View Report".
            blocks += self._section(
                f"📄 *Report*\n<{content.report_link}|View Report>"
            )

        blocks += self._section(f"🆔 *Operation ID*\n`{content.identifier}`")
        if content.identifier_help:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": content.identifier_help}
                    ],
                }
            )

        blocks += self._debug_blocks(content)
        return blocks

    # ------------------------------------------------------------------
    # Pieces
    # ------------------------------------------------------------------

    @staticmethod
    def _section(text: str) -> list:
        """A divider plus one mrkdwn section — the card's repeating unit."""
        return [
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        ]

    @staticmethod
    def _findings_text(content: NotificationContent) -> str:
        """One section: each severity on its own line, then the finding count."""
        summary = content.findings
        rows = [
            f"{_SEVERITY_ICON[severity]} {severity.value.capitalize()}  "
            f"`{summary.count_of(severity)}`"
            for severity in SEVERITY_ORDER
        ]
        rows.append(f"*Total Findings*  `{summary.finding_count}`")
        return "📊 *Findings*\n" + "\n".join(rows)

    def _debug_blocks(self, content: NotificationContent) -> list:
        """Level-4 infrastructure detail, only present when debug mode supplied it."""
        if not content.debug_fields:
            return []
        lines: List[str] = ["*Debug*"]
        lines += [f"{f.label}: {f.value}" for f in content.debug_fields]
        return self._section("\n".join(lines))

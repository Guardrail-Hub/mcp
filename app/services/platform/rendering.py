"""How a platform lifecycle card reads — internal to the platform capability.

Owns the presentation of platform lifecycle events: the card layout, the value
formatting (environment, version), and the two forms every card is produced in —
the plain-text body and the structured :class:`NotificationContent` a rich
transport renders natively.

A *module* rather than a package: the responsibility is real and deserves its
own name, but it is two or three cohesive functions, and the convention prefers
a module until a responsibility clearly owns multiple modules.

Rendering is one-way. Nothing here executes a workflow, decides whether to
notify, or performs I/O — those belong to :class:`PlatformReporter`, which calls
this module.
"""

from typing import Any, Tuple

from app.core.ports.notification_channel import NotificationContent, NotificationField

_HEADER = "🛡️ Guardrail Hub"
_DIVIDER = "━" * 12


def environment_label(value: Any) -> str:
    """Display form of an environment name."""
    return str(value).capitalize() if value else "Unknown"


def version_label(value: Any) -> str:
    """Display form of a version string, ensuring the leading ``v``."""
    text = str(value)
    return text if text.startswith("v") else f"v{text}"


def build_card(
    title: str, fields, integrations=None, footer=None
) -> Tuple[str, NotificationContent]:
    """Build the card in both forms: ``(text, structured_content)``.

    Both are produced from the caller's own values. The structured form is
    never derived from the text — rendering is one-way.
    """
    return (
        _render(title, fields, integrations, footer),
        _content(title, fields, integrations, footer),
    )


def _content(
    title: str, fields, integrations=None, footer=None
) -> NotificationContent:
    """Structured form of the same card :func:`_render` flattens to text.

    Built from the caller's own values — never derived from the rendered
    string — so a transport can lay the card out natively.
    """
    entries = list(fields or [])
    if integrations:
        entries += list(integrations)
    if footer:
        entries += list(footer)
    return NotificationContent(
        title=title,
        fields=tuple(
            NotificationField(label=str(label), value=str(value))
            for label, value in entries
        ),
    )


def _render(title: str, fields, integrations=None, footer=None) -> str:
    lines = [_HEADER, _DIVIDER, title]
    if fields:
        width = max(len(label) for label, _ in fields)
        for label, value in fields:
            lines.append(f"{label.ljust(width)} : {value}")
    if integrations:
        lines.append("Integrations:")
        for label, value in integrations:
            lines.append(f"{label} : {value}")
    lines.append(_DIVIDER)
    if footer:
        for label, value in footer:
            lines.append(f"{label} : {value}")
    return "\n".join(lines)

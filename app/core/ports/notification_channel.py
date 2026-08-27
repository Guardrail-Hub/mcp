"""NotificationChannel port — the outbound boundary for delivering a
notification to an external channel (Slack, Discord, email, ...).

This is a cross-domain hexagonal port. Application code depends only on this
contract, never on a specific chat SDK. Each concrete adapter renders and
delivers according to *its own* capabilities, declared via
:class:`ChannelCapabilities`, so the notification layer can adapt automatically
(update-in-place vs. new-message fallback, progress vs. lifecycle-only) without
any platform-specific branching of its own.

Adding a new platform (Discord, Teams, Telegram, Email, Webhook, Web Dashboard,
...) is only "implement this port + declare capabilities" — no change to the
Notification Service, renderer, or event flow.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.domain.findings import FindingSummary
from app.domain.lifecycle import OperationPhase

# An opaque, channel-native handle to a message already delivered — what a
# subsequent update targets. For Slack it is the message ``ts``; other channels
# use whatever their update API needs. ``None`` means "no handle" (the channel
# cannot, or chose not to, return one).
MessageRef = Optional[str]


@dataclass(frozen=True, slots=True)
class NotificationField:
    """One labelled value in a notification — *what* to say, not how it looks."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class NotificationContent:
    """The structured content of a notification, owned by this port.

    This is what crosses the application/transport boundary. An adapter renders
    it natively (Slack Block Kit, a Discord embed, an HTML email) and **never**
    parses a rendered string back into structure — rendering is one-way.

    The contract lives on the port rather than in either layer, so both the
    application (which builds it) and an infrastructure adapter (which renders
    it) may depend on it without either depending on the other. Domain types
    (:class:`FindingSummary`, :class:`OperationPhase`) are used directly; the
    remaining fields are already-formatted display values, because *how* to
    phrase a duration or a status is a presentation decision the application's
    renderer owns consistently across every transport.

    Attributes:
        title: Headline, including any leading status icon.
        phase: Lifecycle phase this content represents, when it describes an
            operation. Lets a rich adapter pick a layout without inspecting text.
        fields: Ordered labelled values — the generic representation every
            transport can display.
        target: What is being operated on (e.g. ``"GET /api/x"``).
        duration_text: Pre-formatted total duration (e.g. ``"1m 14s"``).
        findings: Canonical severity breakdown, when the operation produced one.
        report_link: URL of the full report, when one exists.
        identifier: Full operation id, surfaced when the user needs to copy it.
        identifier_help: One-line guidance about using ``identifier``.
        reference: Short, non-sensitive reference shown in place of the id.
        debug_fields: Level-4 infrastructure detail; rendered only in debug mode.
    """

    title: str
    phase: Optional[OperationPhase] = None
    fields: Tuple[NotificationField, ...] = field(default_factory=tuple)
    target: Optional[str] = None
    duration_text: Optional[str] = None
    findings: Optional[FindingSummary] = None
    report_link: Optional[str] = None
    identifier: Optional[str] = None
    identifier_help: Optional[str] = None
    reference: Optional[str] = None
    debug_fields: Tuple[NotificationField, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    """What a channel can do, so the Notification Service can adapt to it.

    Defaults are the *safest* assumption (everything unsupported): a channel
    that declares nothing is treated as append-only and lifecycle-only, so an
    under-declared adapter degrades gracefully rather than silently dropping or
    spamming messages.

    Attributes:
        supports_message_update: The channel can edit a previously sent message
            in place (e.g. Slack ``chat.update``). Enables the "one message,
            keep updating it" strategy.
        supports_threads: The channel can group messages in a thread.
        supports_buttons: The channel can render interactive buttons/actions.
        supports_progress: The channel is willing to display incremental
            progress updates. When false, progress snapshots are skipped and
            only lifecycle transitions are delivered.
    """

    supports_message_update: bool = False
    supports_threads: bool = False
    supports_buttons: bool = False
    supports_progress: bool = False


class NotificationChannel(ABC):
    """Contract for delivering an already-rendered message to one channel."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable channel identifier, e.g. ``"slack"`` or ``"discord"``."""

    @property
    def capabilities(self) -> ChannelCapabilities:
        """Declared capabilities of this channel.

        The base implementation returns the all-unsupported default so any
        adapter that does not override it is handled conservatively. Concrete
        adapters override this to advertise what they actually support.
        """
        return ChannelCapabilities()

    @abstractmethod
    def send(
        self,
        destination: str,
        message: str,
        content: Optional[NotificationContent] = None,
    ) -> MessageRef:
        """Deliver a new message to a channel-specific destination.

        Args:
            destination: Channel-native address (e.g. a Slack channel id or a
                thread reference). Opaque to the caller.
            message: Plain-text rendering of the message. Always supplied: it is
                the lowest-common-denominator body every transport can deliver,
                and doubles as the accessibility/notification fallback on
                channels that render richly.
            content: The structured content behind *message*, when the caller
                has it. An adapter capable of native rendering builds from this;
                one that is not simply delivers *message*. An adapter must never
                recover structure by parsing *message*.

        Returns:
            A :data:`MessageRef` handle to the delivered message (for a later
            :meth:`update`), or ``None`` if the channel does not provide one.
        """

    def update(
        self,
        destination: str,
        ref: MessageRef,
        message: str,
        content: Optional[NotificationContent] = None,
    ) -> MessageRef:
        """Edit a previously delivered message in place.

        Only called by the Notification Service when
        :attr:`ChannelCapabilities.supports_message_update` is true, so the base
        implementation raises rather than silently no-op'ing — a channel that
        declares update support but does not implement this is a programming
        error, not a runtime fallback path.

        Args:
            destination: The same channel-native address the message was sent to.
            ref: The handle returned by the original :meth:`send`.
            message: The new plain-text body (see :meth:`send`).
            content: The structured content behind *message*, when available.

        Returns:
            A :data:`MessageRef` for the (still-updatable) message.
        """
        raise NotImplementedError(
            f"channel '{self.name}' does not support message updates"
        )

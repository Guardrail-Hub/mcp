"""Capability-aware operation notifier — the centralized notification strategy.

Subscribes to operation lifecycle + progress domain events, maps each to the
generic :class:`OperationProgress` model, and decides *how* to deliver it based
on the target channel's declared :class:`ChannelCapabilities`. All of the
"which message, update or new, throttle or skip" logic lives here — not in the
dispatcher, not in the tools, not in the channel adapters.

Two strategies, chosen automatically from capabilities:

- **Message-update platforms** (``supports_message_update``): send ONE message
  when the operation is queued, then keep editing that same message on every
  subsequent transition/progress; the final state replaces the last edit. No
  message spam.
- **Fallback platforms** (no update support): send a NEW message only for
  lifecycle transitions (QUEUED -> RUNNING -> COMPLETED | FAILED). Intermediate
  progress is skipped unless the platform explicitly ``supports_progress``.

Progress is additionally throttled (see :class:`ProgressThrottle`) so even a
progress-capable, update-capable channel is edited at most on a stage change,
every ~10%, or every ~30s.

Presentation context (friendly title, target, short reference) is resolved here
and *remembered* per operation, so every edited message carries it even though
only the first (created) event announced the operation's ``batch_type``. The
notifier stays decoupled from the operations domain: the composition root
supplies the ``event name -> OperationPhase`` map AND the ``batch_type -> title``
map, so this unit never hard-codes tool vocabulary. Delivery is best-effort — a
delivery failure is logged and never propagates.
"""

import threading
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Dict, FrozenSet, Mapping, Optional

from app.core.events.contracts import (
    OperationCompleted,
    OperationCreated,
    OperationEvent,
    OperationFailed,
    OperationProgressed,
)
from app.core.ports.event_publisher import DomainEvent
from app.core.ports.notification_channel import ChannelCapabilities, MessageRef
from app.services.notification.message.notification import Notification
from app.services.notification.notification_service import NotificationService
from app.domain.lifecycle import OperationPhase
from app.services.notification.rendering.operation_progress import (
    OperationProgress,
)
from app.services.notification.rendering.renderer import OperationNotificationRenderer
from app.services.notification.policy.throttle import ProgressThrottle


@dataclass
class _OperationState:
    """Per-operation bookkeeping the notifier keeps between events."""

    created_at: float
    throttle: ProgressThrottle
    started_at: Optional[float] = None
    ref: MessageRef = None
    initial_sent: bool = field(default=False)
    # Presentation context, remembered so every edited message keeps showing it.
    title: Optional[str] = None
    target: Optional[str] = None
    last_stage: Optional[str] = None
    # Last known progress %, remembered so the periodic elapsed-time refresh can
    # re-render the same card with an advancing clock (see refresh_active()).
    last_progress: Optional[int] = None
    # Report link, resolved from the persisted result once the operation
    # completes (absent earlier in the lifecycle).
    report_link: Optional[str] = None
    # The exact body last delivered, and when — used to guarantee every update
    # carries new information (skip identical re-renders) and to space out
    # low-value edits (see _deliver / refresh_active).
    last_message: Optional[str] = None
    last_delivered_at: Optional[float] = None


class OperationNotifier:
    """Turns operation events into capability-adapted channel notifications."""

    def __init__(
        self,
        notification_service: NotificationService,
        channel: str,
        destination: str,
        lifecycle_events: Mapping[str, OperationPhase],
        progress_events: FrozenSet[str] = frozenset(),
        *,
        titles: Optional[Mapping[str, str]] = None,
        debug: bool = False,
        renderer: Optional[OperationNotificationRenderer] = None,
        throttle_factory: Callable[[], ProgressThrottle] = ProgressThrottle,
        clock: Callable[[], float] = monotonic,
        refresh_interval_seconds: float = 15.0,
        context_resolver: Optional[Callable[[str], Optional[Mapping[str, str]]]] = None,
        logger=None,
    ) -> None:
        """
        Args:
            notification_service: Delivery + capability lookup facade.
            channel: Target channel name (e.g. ``"slack"``).
            destination: Channel-native destination (e.g. a Slack channel id).
            lifecycle_events: ``event_name -> OperationPhase`` for the guaranteed
                lifecycle transitions. Supplied by the composition root so this
                unit stays decoupled from the operations domain.
            progress_events: Event names treated as throttled, intermediate
                progress (delivered only when the channel supports progress).
            titles: ``batch_type -> friendly title`` map (presentation). Supplied
                by the composition root so the notifier stays tool-agnostic.
            debug: When true, rendered cards include Level-4 infrastructure
                detail. Default false — infrastructure never leaks into the
                standard user experience.
            renderer: Renders an ``OperationProgress`` to a message body. When
                omitted, one is built honouring ``debug``.
            throttle_factory: Builds a fresh throttle per operation.
            clock: Monotonic time source for elapsed/duration (injectable).
            refresh_interval_seconds: How often the background ticker re-renders
                RUNNING operations so their elapsed clock keeps advancing between
                progress events. ``<= 0`` disables the ticker. The ticker is only
                active between :meth:`start` and :meth:`stop`.
            logger: Logger for best-effort delivery failures. Defaults to
                ``MCPLogger("notification")``.
        """
        self._service = notification_service
        self._channel = channel
        self._destination = destination
        self._lifecycle_events = dict(lifecycle_events)
        self._progress_events = frozenset(progress_events)
        self._titles = dict(titles or {})
        self._renderer = renderer or OperationNotificationRenderer(debug=debug)
        self._throttle_factory = throttle_factory
        self._clock = clock
        self._refresh_interval = refresh_interval_seconds
        # Optional read-only resolver (operation_id -> {"target", "title"}) used
        # to enrich a card with presentation context the events don't carry (the
        # scan target above all). Supplied by the composition root so the
        # notifier stays decoupled from the operations/DAO layer.
        self._context_resolver = context_resolver
        self._logger = logger
        self._state: Dict[str, _OperationState] = {}
        self._capabilities: Optional[ChannelCapabilities] = None
        # Guards _state and delivery: lifecycle/progress events arrive on scan
        # worker threads while the refresh ticker runs on its own thread.
        self._lock = threading.RLock()
        self._ticker_stop = threading.Event()
        self._ticker_thread: Optional[threading.Thread] = None

    @property
    def event_names(self) -> tuple[str, ...]:
        """Every event name this notifier reacts to (lifecycle + progress)."""
        return tuple(self._lifecycle_events) + tuple(self._progress_events)

    def handle(self, event: DomainEvent) -> None:
        """Route one domain event to lifecycle or progress delivery.

        Unknown events are ignored. All delivery is best-effort: any error is
        logged and swallowed so a channel problem never breaks the event flow.
        """
        if not isinstance(event, OperationEvent) or not event.operation_id:
            return
        name = type(event).name

        try:
            with self._lock:
                if name in self._progress_events:
                    self._on_progress(event)
                elif name in self._lifecycle_events:
                    self._on_lifecycle(event, self._lifecycle_events[name])
        except Exception as exc:  # noqa: BLE001 - notifications must never crash flow
            self._log_failure(event.operation_id, exc)

    # ------------------------------------------------------------------
    # Elapsed-time refresh (keeps the running card's clock advancing)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background elapsed-time refresh ticker (idempotent).

        No-op when the refresh interval is non-positive or the target channel
        cannot edit messages in place (there would be nothing to refresh without
        posting new messages, which this must never do).
        """
        if self._refresh_interval <= 0 or self._ticker_thread is not None:
            return
        if not self._supports_update():
            return
        self._ticker_stop.clear()
        self._ticker_thread = threading.Thread(
            target=self._ticker_loop,
            name="notification-elapsed-refresh",
            daemon=True,
        )
        self._ticker_thread.start()

    def stop(self) -> None:
        """Stop the background refresh ticker (idempotent, best-effort)."""
        self._ticker_stop.set()
        thread = self._ticker_thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._ticker_thread = None

    def refresh_active(self) -> None:
        """Re-render every RUNNING operation so its elapsed time keeps advancing.

        Edits the existing message in place (never posts a new one) and only on
        update-capable channels, so it is safe to call on any cadence. Elapsed
        time is recomputed from the operation's start anchor at render time, so
        the clock advances even when no progress event has arrived.
        """
        if not self._supports_update():
            return
        now = self._clock()
        with self._lock:
            for operation_id, state in list(self._state.items()):
                if state.started_at is None or not state.initial_sent or state.ref is None:
                    continue
                # Don't pile an elapsed-only edit on top of a recent real update:
                # if a lifecycle/progress event delivered within the last refresh
                # interval, skip this tick so edits stay ~one per interval.
                if (
                    state.last_delivered_at is not None
                    and now - state.last_delivered_at < self._refresh_interval
                ):
                    continue
                # Re-render from the last known progress: a synthetic event
                # carrying remembered state, so the snapshot path stays typed.
                event = OperationProgressed(
                    operation_id=operation_id,
                    stage=state.last_stage,
                    progress=state.last_progress,
                )
                snapshot = self._snapshot(
                    operation_id, OperationPhase.RUNNING, event, state
                )
                self._deliver(state, snapshot, is_progress=True)

    def _ticker_loop(self) -> None:
        # wait() returns True when stop is signalled, False on timeout → refresh.
        while not self._ticker_stop.wait(self._refresh_interval):
            try:
                self.refresh_active()
            except Exception as exc:  # noqa: BLE001 - ticker must never die
                self._log_failure("elapsed-refresh", exc)

    def _supports_update(self) -> bool:
        """Whether the target channel can edit messages in place (guarded)."""
        try:
            return self._caps().supports_message_update
        except Exception:  # noqa: BLE001 - unknown channel etc. → no refresh
            return False

    # ------------------------------------------------------------------
    # Lifecycle transitions (always delivered)
    # ------------------------------------------------------------------

    def _on_lifecycle(self, event: OperationEvent, phase: OperationPhase) -> None:
        operation_id = event.operation_id
        state = self._ensure_state(operation_id)
        self._absorb_context(state, event)
        if phase is OperationPhase.RUNNING and state.started_at is None:
            state.started_at = self._clock()
        if phase is OperationPhase.COMPLETED:
            # Re-resolve context now that the result is persisted, to pick up
            # result-derived presentation fields (e.g. the report link).
            self._seed_context(state, operation_id)

        snapshot = self._snapshot(operation_id, phase, event, state)
        self._deliver(state, snapshot, is_progress=False)

        if phase.is_terminal:
            self._state.pop(operation_id, None)

    # ------------------------------------------------------------------
    # Intermediate progress (throttled, skipped if unsupported)
    # ------------------------------------------------------------------

    def _on_progress(self, event: OperationEvent) -> None:
        if not self._caps().supports_progress:
            return
        operation_id = event.operation_id
        state = self._ensure_state(operation_id)
        self._absorb_context(state, event)
        if state.started_at is None:
            state.started_at = self._clock()

        stage = event.stage if isinstance(event, OperationProgressed) else None
        progress = event.progress if isinstance(event, OperationProgressed) else None
        if not state.throttle.should_emit(stage, progress):
            return

        snapshot = self._snapshot(operation_id, OperationPhase.RUNNING, event, state)
        self._deliver(state, snapshot, is_progress=True)

    # ------------------------------------------------------------------
    # Delivery (the message-update vs. new-message decision)
    # ------------------------------------------------------------------

    def _deliver(
        self, state: _OperationState, snapshot: OperationProgress, is_progress: bool
    ) -> None:
        message = self._renderer.render(snapshot)

        # Every delivery must carry new information. If the freshly rendered body
        # is identical to what the user is already looking at, there is nothing
        # meaningful to say — skip the transport call entirely (no spam, no
        # wasted API calls). The first message always sends (last_message None).
        # The plain-text card is the comparison key because it is the flattened
        # form of exactly the same information the structured content carries.
        if state.last_message is not None and message == state.last_message:
            return

        notification = Notification(
            channel=self._channel,
            destination=self._destination,
            message=message,
            content=self._renderer.render_content(snapshot),
        )
        caps = self._caps()

        if caps.supports_message_update and state.initial_sent:
            # Same operation, updatable channel: edit the one message in place.
            state.ref = self._service.update(notification, state.ref)
            self._record_delivery(state, message)
            return

        # First message for this operation, OR a fallback (append-only) channel.
        # Fallback progress never reaches here: _on_progress returns early unless
        # supports_progress, and an append-only+progress channel posts a new
        # (throttled) message, which is the intended behaviour.
        ref = self._service.notify(notification)
        self._record_delivery(state, message)
        if caps.supports_message_update:
            state.ref = ref
            state.initial_sent = True

    def _record_delivery(self, state: _OperationState, message: str) -> None:
        """Remember the body and time of the last actually-delivered update."""
        state.last_message = message
        state.last_delivered_at = self._clock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_state(self, operation_id: str) -> _OperationState:
        state = self._state.get(operation_id)
        if state is None:
            state = self._state[operation_id] = _OperationState(
                created_at=self._clock(), throttle=self._throttle_factory()
            )
            # Seed presentation context (target/title) once, when the operation
            # is first seen — events alone don't carry the scan target.
            self._seed_context(state, operation_id)
        return state

    def _seed_context(self, state: _OperationState, operation_id: str) -> None:
        """Resolve target/title from the injected resolver (best-effort, once)."""
        if self._context_resolver is None:
            return
        try:
            context = self._context_resolver(operation_id) or {}
        except Exception:  # noqa: BLE001 - context is optional, never break flow
            return
        if state.target is None and context.get("target"):
            state.target = context["target"]
        if state.title is None and context.get("title"):
            state.title = context["title"]
        if state.report_link is None and context.get("report_link"):
            state.report_link = context["report_link"]

    def _absorb_context(self, state: _OperationState, event: OperationEvent) -> None:
        """Remember presentation context as it arrives across the lifecycle.

        Only two event contracts carry context: ``OperationCreated`` announces
        the ``batch_type`` a friendly title is resolved from, and
        ``OperationProgressed`` carries the current stage/percentage. The scan
        target is *not* an event field — it is resolved from the persisted
        operation by the injected context resolver (see :meth:`_seed_context`).
        """
        if isinstance(event, OperationCreated) and state.title is None:
            if event.batch_type:
                state.title = self._titles.get(event.batch_type)
        elif isinstance(event, OperationProgressed):
            if event.stage:
                state.last_stage = event.stage
            if event.progress is not None:
                state.last_progress = event.progress

    def _snapshot(
        self,
        operation_id: str,
        phase: OperationPhase,
        event: OperationEvent,
        state: _OperationState,
    ) -> OperationProgress:
        """Map a typed event plus remembered state onto the progress model.

        Every field is read from a declared contract attribute. Fields the
        contracts do not carry (target, report link) come from ``state``, which
        the injected context resolver populates from the persisted operation —
        they were never event data, and typing the bus made that explicit.
        """
        elapsed = None
        duration = None
        anchor = state.started_at if state.started_at is not None else state.created_at
        if phase is OperationPhase.RUNNING and anchor is not None:
            elapsed = self._clock() - anchor
        if phase.is_terminal and anchor is not None:
            duration = self._clock() - anchor

        progressed = event if isinstance(event, OperationProgressed) else None

        return OperationProgress(
            operation_id=operation_id,
            phase=phase,
            stage=(progressed.stage if progressed else None) or state.last_stage,
            progress=progressed.progress if progressed else None,
            message=progressed.message if progressed else None,
            title=state.title,
            target=state.target,
            reference=self._short_reference(operation_id),
            worker_id=progressed.worker_id if progressed else None,
            elapsed_seconds=elapsed,
            duration_seconds=duration,
            findings=event.findings if isinstance(event, OperationCompleted) else None,
            report_link=state.report_link,
            reason=event.error if isinstance(event, OperationFailed) else None,
            failed_phase=state.last_stage if phase is OperationPhase.FAILED else None,
        )

    @staticmethod
    def _short_reference(operation_id: str) -> str:
        """A short, non-sensitive human reference derived from the operation id.

        Drops any ``"<batch_type>:"`` prefix and keeps the first 8 alphanumeric
        characters, upper-cased (e.g. ``"api_scan:1d015005-..."`` -> ``"1D015005"``).
        """
        tail = operation_id.rsplit(":", 1)[-1]
        alnum = "".join(c for c in tail if c.isalnum())
        return (alnum[:8] or operation_id[:8]).upper()

    def _caps(self) -> ChannelCapabilities:
        if self._capabilities is None:
            self._capabilities = self._service.capabilities(self._channel)
        return self._capabilities

    def _log_failure(self, operation_id: str, exc: Exception) -> None:
        logger = self._logger
        if logger is None:
            from app.core.mcp_logger import MCPLogger  # noqa: PLC0415

            logger = self._logger = MCPLogger("notification")
        try:
            logger.error(
                "Operation notification delivery failed",
                extra={
                    "event": "notification_failed",
                    "operation_id": operation_id,
                    "reason": str(exc),
                },
            )
        except TypeError:
            logger.error(f"Operation notification delivery failed: {exc}")
        except Exception:  # pragma: no cover - logging must never raise
            pass

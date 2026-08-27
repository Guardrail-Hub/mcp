"""Typed operation event contracts.

The canonical schema of every event the application publishes. These are
**Contracts**: they belong to neither the producer nor the consumer, which is
why they live in ``core/`` beside the port they travel through
([Decision 0007](../../../../.ai/decisions/0007-domain-layer-justification.md)
section 3.4). The operations context publishes them; the notification context
subscribes to them; neither imports the other.

Properties every event here holds:

* **Typed** — each event declares its own fields. There is no ``payload``
  dictionary and no string key lookup, so a producer/consumer mismatch fails
  loudly at the attribute rather than silently yielding ``None``.
* **Immutable** — frozen dataclasses. An event records something that already
  happened; it cannot be edited afterwards.
* **Framework-independent** — plain dataclasses. No Pydantic, no ORM, no
  serialization framework.
* **Transport-independent** — nothing here knows about Slack, HTTP, or the
  in-process dispatcher. The same contract survives a move to a broker.
* **Versionable** — each event carries a ``version``. Adding an optional field
  is a compatible change; changing a field's meaning is a version bump.

Events express business meaning: they say *what happened to an operation*, not
what any consumer should do about it. Presentation concerns (titles, targets,
report links) are deliberately absent — those are resolved by the consumer, not
carried on the wire.
"""

from dataclasses import dataclass
from typing import ClassVar, Optional

from app.core.ports.event_publisher import DomainEvent
from app.domain.findings import FindingSummary


@dataclass(frozen=True)
class OperationEvent(DomainEvent):
    """Base for every event about a single operation.

    ``operation_id`` has a default only because the base class contributes a
    defaulted ``occurred_at`` field ahead of it; it is required in practice and
    every publisher supplies it.
    """

    name: ClassVar[str] = "operation.event"

    operation_id: str = ""


@dataclass(frozen=True)
class OperationCreated(OperationEvent):
    """An operation was accepted and persisted as QUEUED."""

    name: ClassVar[str] = "operation.created"

    batch_type: str = ""


@dataclass(frozen=True)
class OperationStarted(OperationEvent):
    """An operation began executing."""

    name: ClassVar[str] = "operation.started"


@dataclass(frozen=True)
class OperationProgressed(OperationEvent):
    """Intermediate progress within a running operation.

    Purely additive to the lifecycle: a tool that cannot report progress simply
    never publishes this, and consumers that do not care ignore it.
    """

    name: ClassVar[str] = "operation.progress"

    stage: Optional[str] = None
    progress: Optional[int] = None
    message: Optional[str] = None
    worker_id: Optional[str] = None


@dataclass(frozen=True)
class OperationCompleted(OperationEvent):
    """An operation finished successfully.

    ``findings`` carries the canonical severity breakdown when the tool produced
    one, so a consumer can summarise the outcome without loading or parsing the
    persisted result. Tools with no findings omit it.
    """

    name: ClassVar[str] = "operation.completed"

    findings: Optional[FindingSummary] = None


@dataclass(frozen=True)
class OperationFailed(OperationEvent):
    """An operation ended in failure."""

    name: ClassVar[str] = "operation.failed"

    error: str = ""


@dataclass(frozen=True)
class OperationCancelled(OperationEvent):
    """An operation was cancelled before completing."""

    name: ClassVar[str] = "operation.cancelled"


# Every operation event contract, for composition-root wiring and diagnostics.
OPERATION_EVENTS = (
    OperationCreated,
    OperationStarted,
    OperationProgressed,
    OperationCompleted,
    OperationFailed,
    OperationCancelled,
)

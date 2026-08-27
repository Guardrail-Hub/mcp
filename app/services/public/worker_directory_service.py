"""Reads every engine's worker registry and presents one list.

**Read-only, and that is the whole design.** Nothing here registers, drains,
removes, assigns, or mutates anything. The registries stay independent and stay
authoritative for their own pools; this walks them and copies what it finds into
:class:`UnifiedWorkerInfo`. An operator gets one page; the engines get no shared
lifecycle.

**Adding an engine is one row.** ``_default_registries()`` is a table of
``(worker_type, registry)`` pairs, not a plugin point. A third engine appends a
tuple. There is no base class, no protocol and no registration decorator,
because the only thing the two registries have to share is a ``list_all()`` that
returns objects with the fields the projection reads — and that is a fact about
them today, not a contract anyone should be asked to implement (ADR-0009's
reasoning about abstracting over a set of resemblances).

Ordering is ``(worker_type, hostname, worker_id)``. Each registry already
returns its own workers sorted by ``worker_id``, so appending them would have
been deterministic too — but a flat list interleaved by engine is what an
operator scanning for "where are my JMeter workers" actually needs, and sorting
here means the answer does not change if a registry's internal order ever does.
"""

from typing import Iterable, List, Optional, Sequence, Tuple

from app.schemas.public.worker_directory import UnifiedWorkerInfo

#: A worker may take new work only from this lifecycle state.
_AVAILABLE_STATE = "active"


class WorkerDirectoryService:
    """Aggregates the per-engine worker registries into one read-only view."""

    def __init__(
        self, registries: Optional[Sequence[Tuple[str, object]]] = None
    ) -> None:
        """
        Args:
            registries: ``(worker_type, registry)`` pairs to read. Defaults to
                every engine the server runs. Injectable so the aggregation can
                be tested without process-wide singletons.
        """
        self._registries = tuple(
            registries if registries is not None else self._default_registries()
        )

    @staticmethod
    def _default_registries() -> Tuple[Tuple[str, object], ...]:
        """The engines this server has. Add a row to add an engine.

        Imported inside the function so constructing this service does not drag
        both engines' process-wide singletons into module import order.
        """
        from app.integrations.jmeter.runtime import (  # noqa: PLC0415
            registry as jmeter_registry,
        )
        from app.integrations.owasp_zap.runtime import (  # noqa: PLC0415
            registry as zap_registry,
        )

        return (
            ("zap", zap_registry),
            ("jmeter", jmeter_registry),
        )

    # ── Read ─────────────────────────────────────────────────────────────

    def list_workers(self) -> List[UnifiedWorkerInfo]:
        """Every registered worker, from every engine, in a stable order.

        An engine with no workers contributes nothing rather than an error: an
        empty pool is the normal state of a server whose workers have not been
        started, not a fault worth failing an operator's dashboard over.
        """
        workers: List[UnifiedWorkerInfo] = []
        for worker_type, registry in self._registries:
            workers.extend(self._project(worker_type, registry.list_all()))

        return sorted(
            workers, key=lambda w: (w.worker_type, w.hostname, w.worker_id)
        )

    # ── Projection ───────────────────────────────────────────────────────

    @classmethod
    def _project(
        cls, worker_type: str, workers: Iterable
    ) -> List[UnifiedWorkerInfo]:
        """Copy each engine's worker record into the unified shape.

        Reads only; the source objects are never written to, and the returned
        models are new instances rather than views onto registry state.
        """
        return [
            UnifiedWorkerInfo(
                worker_type=worker_type,
                worker_id=worker.worker_id,
                hostname=worker.hostname,
                endpoint=worker.endpoint,
                port=worker.port,
                state=cls._state_of(worker),
                op_id=worker.op_id,
                available=cls._is_available(worker),
                last_heartbeat=worker.last_heartbeat,
            )
            for worker in workers
        ]

    @staticmethod
    def _state_of(worker) -> str:
        """Both engines use a ``str`` enum with the same three values."""
        state = worker.state
        return state.value if hasattr(state, "value") else str(state)

    @classmethod
    def _is_available(cls, worker) -> bool:
        """Idle *and* accepting work.

        Both conditions are needed and neither implies the other: a DRAINING
        worker finishing its last operation is busy but not available, and a
        DRAINING worker that has already been released is idle but still must
        not be given anything new.
        """
        return worker.op_id is None and cls._state_of(worker) == _AVAILABLE_STATE

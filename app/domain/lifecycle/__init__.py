"""Lifecycle capability — what an Operation's life looks like.

Owner: Operations capability (Decision 0008 section 7).

Allowed here: the phase vocabulary and the rule for which phases are terminal.

Explicitly NOT allowed: persistence concerns, event names or payload shapes
(those are ``core/`` contracts), notification or presentation fields, tool
vocabulary (``batch_type``, ``operation_type``), Pydantic, or any import from
``services``.

This capability does **not** depend on :mod:`app.domain.findings`, and must not
— phase transitions are valid for tools that produce no findings at all
(Decision 0008 section 8).
"""

from app.domain.lifecycle.phase import OperationPhase

__all__ = ["OperationPhase"]

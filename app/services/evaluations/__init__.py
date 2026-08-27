"""Evaluations context — public API.

Exposes the :class:`EvaluationService` and the :data:`ScanRunner` callable
type, which is the injection contract a concrete runner implementation must
satisfy (the ZAP-bound runner lives in the ZAP tool package and is wired in
by the composition root).
"""

from app.services.evaluations.evaluation_service import EvaluationService, ScanRunner

__all__ = [
    "EvaluationService",
    "ScanRunner",
]

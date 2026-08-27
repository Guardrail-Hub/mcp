"""
History service — business logic for querying past and in-flight scan operations.
"""
from typing import Optional

from app.dao.operation_dao import get_operation_dao
from app.dao.operation_record import OperationRecord


class HistoryService:
    """Encapsulates all operation-history queries on top of the DAO layer."""

    def __init__(self) -> None:
        self._dao = get_operation_dao()

    def get_result(self, operation_id: str) -> Optional[OperationRecord]:
        """Return the full operation record for *operation_id*, or ``None`` if not found."""
        return self._dao.get_operation(operation_id)

    def list_all(self) -> list[OperationRecord]:
        """Return every recorded operation (``result`` field excluded)."""
        return self._dao.get_all()

    def list_by_type(self, batch_type: str) -> list[OperationRecord]:
        """Return operations whose ``batch_type`` matches *batch_type* (case-insensitive)."""
        return self._dao.get_by_type(batch_type.lower())

    def list_by_status(self, *statuses: str) -> list[OperationRecord]:
        """Return operations whose ``status`` is one of *statuses* (upper-cased)."""
        normalised = [s.strip().upper() for s in statuses if s.strip()]
        return self._dao.get_by_status(*normalised)

    def list_ids_by_type(self, batch_type: str) -> list[str]:
        """Return the operation IDs of all operations whose ``batch_type`` matches *batch_type*."""
        records = self._dao.get_by_type(batch_type.lower())
        return [r.operation_id for r in records]

"""
PostgresOperationDAO
====================
Persists :class:`OperationRecord` objects to a PostgreSQL database.

Configuration (via ``settings``)
---------------------------------
* ``DATABASE_URL``       — full DSN, takes precedence if set.
* ``POSTGRES_HOST``      — host (default ``"postgres"``).
* ``POSTGRES_PORT``      — port (default ``5432``).
* ``POSTGRES_DB``        — database name (default ``"guardrail_hub"``).
* ``POSTGRES_USER``      — username (default ``"guardrail"``).
* ``POSTGRES_PASSWORD``  — password.

Schema
------
The ``operations`` table (and supporting index) is no longer applied by hand —
:meth:`initialize_schema` runs the versioned SQL files under
``app/dao/postgres/migrations/`` on startup (see
``app/dao/postgres/migration_runner.py``) and verifies the table exists
before the server accepts requests. A failed migration aborts startup with a
non-zero exit code rather than serving requests against a missing schema.
"""

import json
from datetime import datetime, timezone
from typing import Any, Collection, Optional

from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.postgres.postgres_dao import PostgresDAO
from app.dao.operation_record import OperationRecord
from app.domain.lifecycle import OperationPhase

# Columns returned by list-style queries (omit heavy ``result`` column)
_LIST_COLUMNS = "operation_id, status, batch_type, metadata, error, created_at, updated_at, log_path"


class PostgresOperationDAO(PostgresDAO, BaseOperationDAO):
    """Save and retrieve operation records from a local / self-hosted PostgreSQL instance."""

    _INSERT_SQL = """
        INSERT INTO operations (
            operation_id, status, batch_type, metadata,
            result, error, created_at, updated_at, log_path
        )
        VALUES (%(operation_id)s, %(status)s, %(batch_type)s, %(metadata)s,
                %(result)s, %(error)s, %(created_at)s, %(updated_at)s, %(log_path)s)
        ON CONFLICT (operation_id) DO NOTHING;
    """

    _UPDATE_STATUS_SQL = """
        UPDATE operations
        SET status     = %(status)s,
            error      = %(error)s,
            updated_at = %(updated_at)s
        WHERE operation_id = %(operation_id)s;
    """

    _UPDATE_STATUS_WITH_RESULT_SQL = """
        UPDATE operations
        SET status     = %(status)s,
            result     = %(result)s,
            error      = %(error)s,
            updated_at = %(updated_at)s
        WHERE operation_id = %(operation_id)s;
    """

    def __init__(self) -> None:
        PostgresDAO.__init__(self)
        self._logger = MCPLogger("PostgresOperationDAO")

    # ── BaseOperationDAO ──────────────────────────────────────────────────────

    def create_operation(
        self,
        op_id: str,
        status: str,
        metadata: dict[str, Any],
        batch_type: str,
        log_path: Optional[str] = None,
    ) -> None:
        """
        Insert a new operation record into the ``operations`` table.

        A new connection is opened for each call and closed in a ``finally``
        block to avoid leaking connections.

        Args:
            op_id:      Unique operation identifier (primary key).
            status:     Initial status value.
            metadata:   Arbitrary key-value context (targets, config, etc.).
            batch_type: Category / tool type that produced this operation.
            log_path:   Optional filesystem path to the operation log file.

        Raises:
            RuntimeError: If ``psycopg2`` is not installed.
            psycopg2.Error: On any PostgreSQL driver error.
        """
        self._logger.info("Saving operation '%s' to PostgreSQL", op_id)

        now_utc = datetime.now(timezone.utc)
        params: dict[str, Any] = {
            "operation_id": op_id,
            "status": status,
            "batch_type": batch_type,
            "metadata": json.dumps(metadata),
            "result": None,
            "error": None,
            "created_at": now_utc,
            "updated_at": now_utc,
            "log_path": log_path or metadata.get("log_path"),
        }

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(self._INSERT_SQL, params)
            self._logger.info("Operation '%s' saved (status=%s)", op_id, status)
        except Exception:
            self._logger.error("Failed to save operation '%s' to PostgreSQL", op_id)
            raise
        finally:
            conn.close()

    def get_operation(self, operation_id: str) -> Optional[OperationRecord]:
        """
        Retrieve a single operation by its primary key.

        Args:
            operation_id: The unique operation identifier.

        Returns:
            The :class:`OperationRecord` if found, otherwise ``None``.
        """
        self._logger.info("Fetching operation '%s' from PostgreSQL", operation_id)

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT operation_id, status, batch_type, metadata, result, error, "
                    "created_at, updated_at, log_path FROM operations "
                    "WHERE operation_id = %(operation_id)s",
                    {"operation_id": operation_id},
                )
                row = cur.fetchone()
                if row is None:
                    self._logger.info("Operation '%s' not found", operation_id)
                    return None
                return self._from_row(dict(zip([d[0] for d in cur.description], row)))
        except Exception:
            self._logger.error("Failed to fetch operation '%s' from PostgreSQL", operation_id)
            raise
        finally:
            conn.close()

    def update_operation_status(
        self,
        operation_id: str,
        status: str,
        result: Optional[Any] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
    ) -> None:
        """
        Partially update *status*, *result*, and *error* for an existing operation.

        ``updated_at`` is always refreshed to the current UTC time.

        Args:
            operation_id: The unique operation identifier.
            status:       New status value.
            result:       Optional result payload to store (JSON-serialised).
            error:        Optional error message to store.
            clear_error:  When ``True``, explicitly sets ``error = NULL``
                          regardless of the *error* argument.
        """
        self._logger.info(
            "Updating operation '%s' → status=%s in PostgreSQL", operation_id, status
        )

        params: dict[str, Any] = {
            "operation_id": operation_id,
            "status": status,
            "error": None if clear_error else error,
            "updated_at": datetime.now(timezone.utc),
        }

        if result is not None:
            sql = self._UPDATE_STATUS_WITH_RESULT_SQL
            params["result"] = json.dumps(result)
        else:
            sql = self._UPDATE_STATUS_SQL

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            self._logger.info("Operation '%s' updated (status=%s)", operation_id, status)
        except Exception:
            self._logger.error("Failed to update operation '%s' in PostgreSQL", operation_id)
            raise
        finally:
            conn.close()

    def get_all(self) -> list[OperationRecord]:
        """
        Return every operation record (without the ``result`` field).

        Returns:
            A list of :class:`OperationRecord` objects (may be empty).
        """
        self._logger.info("Fetching all operations from PostgreSQL")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_LIST_COLUMNS} FROM operations ORDER BY created_at DESC")
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
                return [self._from_row(dict(zip(columns, row))) for row in rows]
        except Exception:
            self._logger.error("Failed to fetch all operations from PostgreSQL")
            raise
        finally:
            conn.close()

    def get_by_type(self, batch_type: str) -> list[OperationRecord]:
        """
        Return all operation records whose ``batch_type`` matches *batch_type*.

        Args:
            batch_type: The batch type to filter by.

        Returns:
            A (possibly empty) list of matching :class:`OperationRecord` objects.
        """
        self._logger.info(
            "Fetching operations by batch_type='%s' from PostgreSQL", batch_type
        )

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_LIST_COLUMNS} FROM operations "
                    "WHERE batch_type = %(batch_type)s ORDER BY created_at DESC",
                    {"batch_type": batch_type},
                )
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
                return [self._from_row(dict(zip(columns, row))) for row in rows]
        except Exception:
            self._logger.error(
                "Failed to fetch operations by batch_type='%s' from PostgreSQL", batch_type
            )
            raise
        finally:
            conn.close()

    def get_by_status(self, *statuses: str) -> list[OperationRecord]:
        """
        Return all operation records whose ``status`` is one of *statuses*.

        Passing no arguments is equivalent to calling :meth:`get_all`.

        Args:
            *statuses: One or more status strings to filter by.

        Returns:
            A (possibly empty) list of matching :class:`OperationRecord` objects.
        """
        if not statuses:
            return self.get_all()

        self._logger.info(
            "Fetching operations by statuses=%s from PostgreSQL", statuses
        )

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_LIST_COLUMNS} FROM operations "
                    "WHERE status = ANY(%(statuses)s) ORDER BY created_at DESC",
                    {"statuses": list(statuses)},
                )
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
                return [self._from_row(dict(zip(columns, row))) for row in rows]
        except Exception:
            self._logger.error(
                "Failed to fetch operations by statuses=%s from PostgreSQL", statuses
            )
            raise
        finally:
            conn.close()

    def get_queued_operations(
        self, batch_types: Collection[str]
    ) -> list[OperationRecord]:
        """
        Return the ``QUEUED`` operations in *batch_types*, oldest first.

        Uses ``ORDER BY created_at ASC`` directly (backed by
        ``idx_operations_status_created_at``) instead of the base class's
        fetch-then-sort fallback. ``batch_type`` is a residual filter over the
        already-small ``QUEUED`` set, so no additional index is needed.

        Args:
            batch_types: The queue lane this caller consumes (see
                :meth:`app.dao.base.BaseOperationDAO.get_queued_operations`).

        Returns:
            A (possibly empty) list of ``QUEUED`` :class:`OperationRecord` objects
            in the given lane, oldest first.
        """
        # High-frequency dispatcher poll: DEBUG so production logs stay clean.
        # The dispatcher emits INFO only when the queue depth actually changes.
        self._logger.debug("Polling queued operations from PostgreSQL")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_LIST_COLUMNS} FROM operations "
                    "WHERE status = %(status)s AND batch_type = ANY(%(batch_types)s) "
                    "ORDER BY created_at ASC",
                    {
                        "status": OperationPhase.QUEUED,
                        "batch_types": list(batch_types),
                    },
                )
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description]
                return [self._from_row(dict(zip(columns, row))) for row in rows]
        except Exception:
            self._logger.error("Failed to fetch queued operations from PostgreSQL")
            raise
        finally:
            conn.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _from_row(row: dict[str, Any]) -> OperationRecord:
        """Deserialise a psycopg2 row dict back to an :class:`OperationRecord`."""
        result = row.get("result")
        if isinstance(result, str):
            result = json.loads(result)

        return OperationRecord(
            operation_id=row["operation_id"],
            status=OperationPhase(row["status"]),
            batch_type=row["batch_type"],
            metadata=row.get("metadata") or {},
            result=result,
            error=row.get("error"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            log_path=row.get("log_path"),
        )

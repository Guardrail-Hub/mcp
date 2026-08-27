"""
DynamoDBOperationDAO
====================
Persists :class:`OperationRecord` objects to AWS DynamoDB (or DynamoDB Local).

Configuration (via ``settings``)
---------------------------------
* ``AWS_REGION``             — AWS region (default ``"ap-southeast-1"``).
* ``DYNAMODB_TABLE_PREFIX``  — table name prefix (default ``"guardrail_local"``).
                               The actual table used is ``{prefix}_operations``.
* ``DYNAMODB_ENDPOINT_URL``  — override endpoint for DynamoDB Local.
                               Leave empty for real AWS.
* ``AWS_ACCESS_KEY_ID``      — explicit key for local DynamoDB (optional).
* ``AWS_SECRET_ACCESS_KEY``  — explicit secret for local DynamoDB (optional).

Required table DDL (AWS CLI)
-----------------------------
.. code-block:: bash

    aws dynamodb create-table \\
        --table-name guardrail_local_operations \\
        --attribute-definitions AttributeName=operation_id,AttributeType=S \\
        --key-schema AttributeName=operation_id,KeyType=HASH \\
        --billing-mode PAY_PER_REQUEST

For DynamoDB Local, prepend ``--endpoint-url http://localhost:8001``.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.mcp_logger import MCPLogger
from app.dao.base import BaseOperationDAO
from app.dao.dynamodb.dynamodb_dao import DynamoDBDAO
from app.dao.operation_record import OperationRecord
from app.domain.lifecycle import OperationPhase

# Projected attributes used by list-style queries (omit heavy ``result`` field)
_LIST_PROJECTION = "operation_id, batch_type, #meta, #stat, created_at, updated_at, #err, log_path"
_LIST_EXPR_NAMES = {
    "#stat": "status",
    "#err": "error",
    "#meta": "metadata",
}


class DynamoDBOperationDAO(DynamoDBDAO, BaseOperationDAO):
    """Save and retrieve operation records from AWS DynamoDB or DynamoDB Local."""

    PARTITION_KEY = "operation_id"

    def __init__(self) -> None:
        table_name = f"{settings.dynamodb_table_prefix}_operations"
        DynamoDBDAO.__init__(self, table_name=table_name)
        self._logger = MCPLogger("DynamoDBOperationDAO")

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
        Put a new operation record into the ``{prefix}_operations`` DynamoDB table.

        Uses ``put_item``, which overwrites an existing item sharing the same
        partition key (``operation_id``), effectively acting as an upsert.

        Attributes whose value is ``None`` are stripped before the call because
        DynamoDB does not allow null values on non-key attributes.

        Args:
            op_id:      Unique operation identifier (primary key).
            status:     Initial status value.
            metadata:   Arbitrary key-value context (targets, config, etc.).
            batch_type: Category / tool type that produced this operation.
            log_path:   Optional filesystem path to the operation log file.

        Raises:
            botocore.exceptions.BotoCoreError: On any AWS / DynamoDB error.
        """
        self._logger.info(
            "Saving operation '%s' to DynamoDB table '%s'",
            op_id,
            self.table.name,
        )

        now_utc = datetime.now(timezone.utc).isoformat()
        raw: dict[str, Any] = {
            "operation_id": op_id,
            "status": status,
            "batch_type": batch_type,
            "metadata": metadata,
            "result": None,
            "error": None,
            "created_at": now_utc,
            "updated_at": now_utc,
            "log_path": log_path or metadata.get("log_path"),
        }
        item = {k: v for k, v in raw.items() if v is not None}

        try:
            self.table.put_item(Item=item)
            self._logger.info("Operation '%s' saved (status=%s)", op_id, status)
        except Exception:
            self._logger.error(
                "Failed to save operation '%s' to DynamoDB table '%s'",
                op_id,
                self.table.name,
            )
            raise

    def get_operation(self, operation_id: str) -> Optional[OperationRecord]:
        """
        Retrieve a single operation by its primary key.

        Args:
            operation_id: The unique operation identifier.

        Returns:
            The :class:`OperationRecord` if found, otherwise ``None``.
        """
        self._logger.info("Fetching operation '%s' from DynamoDB", operation_id)

        try:
            response = self.table.get_item(Key={self.PARTITION_KEY: operation_id})
            item = response.get("Item")
            if item is None:
                self._logger.info("Operation '%s' not found", operation_id)
                return None
            return self._from_item(item)
        except Exception:
            self._logger.error("Failed to fetch operation '%s' from DynamoDB", operation_id)
            raise

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
            clear_error:  When ``True``, removes the ``error`` attribute entirely
                          using a DynamoDB ``REMOVE`` expression.
        """
        now_utc = datetime.now(timezone.utc).isoformat()

        update_expression = "SET #st = :s, #updated_at = :u"
        expression_values: dict[str, Any] = {":s": status, ":u": now_utc}
        expression_names: dict[str, str] = {
            "#st": "status",
            "#updated_at": "updated_at",
            "#err": "error",
        }

        if not clear_error:
            update_expression += ", #err = :e"
            expression_values[":e"] = error

        if result is not None:
            update_expression += ", #res = :r"
            expression_values[":r"] = json.dumps(result)
            expression_names["#res"] = "result"

        if clear_error:
            update_expression += " REMOVE #err"

        self._logger.info("Updating operation '%s' → status=%s", operation_id, status)

        try:
            self.table.update_item(
                Key={self.PARTITION_KEY: operation_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames=expression_names,
                ReturnValues="UPDATED_NEW",
            )
            self._logger.info("Operation '%s' updated (status=%s)", operation_id, status)
        except Exception:
            self._logger.error("Failed to update operation '%s' in DynamoDB", operation_id)
            raise

    def get_all(self) -> list[OperationRecord]:
        """
        Return every operation record (without the ``result`` field).

        Uses a paginated DynamoDB scan to handle tables larger than 1 MB.

        Returns:
            A list of :class:`OperationRecord` objects (may be empty).
        """
        self._logger.info("Scanning all operations from DynamoDB")

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": _LIST_PROJECTION,
            "ExpressionAttributeNames": dict(_LIST_EXPR_NAMES),
        }

        try:
            items = self._paginated_scan(scan_kwargs)
            return [self._from_item(i) for i in items]
        except Exception:
            self._logger.error("Failed to scan all operations from DynamoDB")
            raise

    def get_by_type(self, batch_type: str) -> list[OperationRecord]:
        """
        Return all operation records whose ``batch_type`` matches *batch_type*.

        Args:
            batch_type: The batch type to filter by.

        Returns:
            A (possibly empty) list of matching :class:`OperationRecord` objects.
        """
        self._logger.info(
            "Scanning operations by batch_type='%s' from DynamoDB", batch_type
        )

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": _LIST_PROJECTION,
            "ExpressionAttributeNames": {**_LIST_EXPR_NAMES, "#bt": "batch_type"},
            "FilterExpression": "#bt = :bt",
            "ExpressionAttributeValues": {":bt": batch_type},
        }

        try:
            items = self._paginated_scan(scan_kwargs)
            return [self._from_item(i) for i in items]
        except Exception:
            self._logger.error(
                "Failed to scan operations by batch_type='%s' from DynamoDB", batch_type
            )
            raise

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

        # DEBUG: status scans are driven by the high-frequency dispatcher poll
        # (get_queued_operations). Keeping this at DEBUG avoids flooding
        # production logs; meaningful queue changes are reported by the
        # dispatcher at INFO instead.
        self._logger.debug(
            "Scanning operations by statuses=%s from DynamoDB", statuses
        )

        filter_expression = " OR ".join(f"#st = :st{i}" for i in range(len(statuses)))
        attr_values = {f":st{i}": s for i, s in enumerate(statuses)}

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": _LIST_PROJECTION,
            "ExpressionAttributeNames": {**_LIST_EXPR_NAMES, "#st": "status"},
            "FilterExpression": filter_expression,
            "ExpressionAttributeValues": attr_values,
        }

        try:
            items = self._paginated_scan(scan_kwargs)
            return [self._from_item(i) for i in items]
        except Exception:
            self._logger.error(
                "Failed to scan operations by statuses=%s from DynamoDB", statuses
            )
            raise

    # get_queued_operations() is inherited from BaseOperationDAO: a status-filtered
    # scan, then lane filtering and created_at ordering in Python. DynamoDB scans
    # have no native ordering, so the base implementation is exactly what this
    # backend would have written. Pushing batch_type into the scan's
    # FilterExpression would cut items transferred, not capacity consumed — do it
    # if queue depth ever makes that visible.

    def initialize_schema(self) -> None:
        """
        Verify the operations table exists before the server accepts requests.

        Against real AWS, table provisioning is expected to be managed by
        infrastructure-as-code — this only verifies and raises if missing.
        Against DynamoDB Local / an emulator (``DYNAMODB_ENDPOINT_URL`` set),
        the table is created automatically so a fresh local environment works
        without a manual step.

        Raises:
            RuntimeError: If the table is missing and cannot (or should not)
                be auto-created.
        """
        try:
            self.table.load()
            self._logger.info("Verified DynamoDB table '%s' exists", self.table.name)
            return
        except Exception as exc:  # noqa: BLE001 - botocore raises ClientError here
            if not settings.dynamodb_endpoint_url:
                raise RuntimeError(
                    f"DynamoDB table '{self.table.name}' does not exist and cannot "
                    "be auto-created against real AWS — provision it via "
                    "infrastructure-as-code before starting the server."
                ) from exc

        self._logger.info(
            "DynamoDB table '%s' missing — creating it (local endpoint)", self.table.name
        )
        client = self.table.meta.client
        client.create_table(
            TableName=self.table.name,
            AttributeDefinitions=[
                {"AttributeName": self.PARTITION_KEY, "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": self.PARTITION_KEY, "KeyType": "HASH"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName=self.table.name)
        self._logger.info("Created DynamoDB table '%s'", self.table.name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _paginated_scan(self, scan_kwargs: dict[str, Any]) -> list[dict[str, Any]]:
        """Iterate through all DynamoDB pages using ``self.table`` and return combined items."""
        items: list[dict[str, Any]] = []
        start_key = None

        while True:
            if start_key:
                scan_kwargs["ExclusiveStartKey"] = start_key
            response = self.table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")
            if start_key is None:
                break

        return items

    @staticmethod
    def _from_item(item: dict[str, Any]) -> OperationRecord:
        """Deserialise a DynamoDB item dict back to an :class:`OperationRecord`."""
        return OperationRecord(
            operation_id=item["operation_id"],
            status=OperationPhase(item["status"]),
            batch_type=item["batch_type"],
            metadata=item.get("metadata", {}),
            result=json.loads(item["result"]) if item.get("result") else None,
            error=item.get("error"),
            created_at=datetime.fromisoformat(item["created_at"]),
            updated_at=datetime.fromisoformat(item["updated_at"]),
            log_path=item.get("log_path"),
        )

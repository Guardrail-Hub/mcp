"""
OperationDAO factory
====================
Returns the correct :class:`BaseOperationDAO` implementation based on the
``DATABASE_PROVIDER`` setting.

Usage
-----
.. code-block:: python

    from app.constants.batch import BatchType
    from app.dao.operation_dao import get_operation_dao
    from app.domain.lifecycle import OperationPhase

    dao = get_operation_dao()
    dao.create_operation(
        op_id="zap_api_scan:abc123",
        status=OperationPhase.QUEUED,
        batch_type=BatchType.API_SCAN,
        metadata={"target_url": "https://api.example.com"},
        log_path="logs/ZapApiScanService_20260615.log",
    )

Routing key
-----------
``DATABASE_PROVIDER`` env var → ``settings.database_provider``:

* ``"POSTGRES"``  → :class:`~app.dao.postgres.operation_dao.PostgresOperationDAO`
* ``"DYNAMODB"``  → :class:`~app.dao.dynamodb.operation_dao.DynamoDBOperationDAO`
* ``"NONE"``      → raises :class:`RuntimeError`
"""

from app.core.config import settings
from app.dao.base import BaseOperationDAO

from app.dao.postgres.operation_dao import PostgresOperationDAO  # noqa: PLC0415
from app.dao.dynamodb.operation_dao import DynamoDBOperationDAO  # noqa: PLC0415

def get_operation_dao() -> BaseOperationDAO:
    """
    Resolve and return the active :class:`BaseOperationDAO` implementation.

    The selection is driven by ``settings.database_provider``:

    * ``"POSTGRES"``  → local PostgreSQL via ``psycopg2``
    * ``"DYNAMODB"``  → AWS / DynamoDB Local via ``boto3``

    Returns:
        A concrete ``BaseOperationDAO`` instance ready to use.

    Raises:
        RuntimeError: If ``DATABASE_PROVIDER`` is ``"NONE"`` or unsupported.
    """
    provider = settings.database_provider

    if provider == "POSTGRES":
        return PostgresOperationDAO()

    if provider == "DYNAMODB":
        return DynamoDBOperationDAO()

    raise RuntimeError(
        f"DATABASE_PROVIDER is '{provider}' — no OperationDAO available. "
        "Set it to 'POSTGRES' (local) or 'DYNAMODB' (remote) in your .env."
    )

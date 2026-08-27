"""
DynamoDBDAO
===========
Infrastructure base class that bootstraps a boto3 DynamoDB Table resource
and exposes it as ``self.table`` for subclasses to use directly.

Configuration (via ``settings``)
---------------------------------
* ``AWS_REGION``            — AWS region (default ``"ap-southeast-1"``).
* ``DYNAMODB_ENDPOINT_URL`` — override endpoint for DynamoDB Local / testing.
                              Leave empty to target real AWS.

AWS credentials
---------------
When ``DYNAMODB_ENDPOINT_URL`` is **not** set, credentials are resolved via
the standard boto3 chain (env vars → ``~/.aws/credentials`` → IAM role).

When ``DYNAMODB_ENDPOINT_URL`` **is** set (local mode), explicit key/secret
from ``settings.aws_access_key_id`` / ``settings.aws_secret_access_key``
are forwarded so that tools like DynamoDB Local or LocalStack work without
real credentials.
"""

from typing import Any

from app.core.config import settings
from app.core.mcp_logger import MCPLogger


class DynamoDBDAO:
    """
    Lightweight infrastructure base for DynamoDB-backed DAOs.

    Subclasses call ``super().__init__(table_name=...)`` and then access
    ``self.table`` to perform DynamoDB operations without needing to
    repeat the boto3 setup boilerplate.

    Example
    -------
    .. code-block:: python

        class MyDAO(DynamoDBDAO):
            def __init__(self) -> None:
                super().__init__(table_name="my_table")

            def get_item(self, key: str):
                return self.table.get_item(Key={"key": key})
    """

    def __init__(self, table_name: str) -> None:
        try:
            import boto3  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "boto3 is not installed. Run: uv add boto3"
            ) from exc

        logger = MCPLogger("DynamoDBDAO")

        dynamodb = boto3.resource("dynamodb", **self._session_kwargs())
        self.table = dynamodb.Table(table_name)

        logger.info("DynamoDBDAO initialised — table: '%s'", table_name)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _session_kwargs() -> dict[str, Any]:
        """
        Build boto3 keyword arguments from ``settings``.

        * Always sets ``region_name``.
        * When ``dynamodb_endpoint_url`` is set (local / test mode), also
          forwards explicit ``aws_access_key_id`` and ``aws_secret_access_key``
          so local DynamoDB emulators work without real AWS credentials.
        """
        kwargs: dict[str, Any] = {"region_name": settings.aws_region}

        if settings.dynamodb_endpoint_url:
            kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
            if settings.aws_access_key_id:
                kwargs["aws_access_key_id"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

        return kwargs

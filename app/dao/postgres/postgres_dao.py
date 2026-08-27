"""
PostgresDAO
===========
Infrastructure base class that manages psycopg2 connection setup and exposes
``self._connect()`` for subclasses to use directly.

Configuration (via ``settings``)
---------------------------------
* ``DATABASE_URL``       — full DSN, takes precedence if set.
* ``POSTGRES_HOST``      — host (default ``"postgres"``).
* ``POSTGRES_PORT``      — port (default ``5432``).
* ``POSTGRES_DB``        — database name (default ``"guardrail_hub"``).
* ``POSTGRES_USER``      — username (default ``"guardrail"``).
* ``POSTGRES_PASSWORD``  — password.
"""

from app.core.config import settings
from app.core.mcp_logger import MCPLogger


class PostgresDAO:
    """
    Lightweight infrastructure base for PostgreSQL-backed DAOs.

    Subclasses call ``super().__init__()`` and then use ``self._connect()``
    to open a new psycopg2 connection without repeating the DSN-building
    and driver-import boilerplate.

    Example
    -------
    .. code-block:: python

        class MyDAO(PostgresDAO):
            def __init__(self) -> None:
                super().__init__()

            def get_item(self, key: str):
                conn = self._connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT * FROM my_table WHERE key = %s", (key,))
                        return cur.fetchone()
                finally:
                    conn.close()
    """

    def __init__(self) -> None:
        logger = MCPLogger("PostgresDAO")
        logger.info(
            "PostgresDAO initialised — host: '%s', db: '%s'",
            settings.postgres_host,
            settings.postgres_db,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _connect(self):
        """
        Import psycopg2 lazily and return a new database connection.

        Raises:
            RuntimeError: If ``psycopg2`` is not installed.
            psycopg2.OperationalError: If the connection cannot be established.
        """
        try:
            import psycopg2  # noqa: PLC0415
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "psycopg2 is not installed. Run: uv add psycopg2-binary"
            ) from exc

        return psycopg2.connect(self._connection_url())

    def _connection_url(self) -> str:
        """Build the PostgreSQL DSN, preferring ``DATABASE_URL`` if set."""
        if settings.database_url:
            return settings.database_url

        password = settings.postgres_password or ""
        return (
            f"postgresql://{settings.postgres_user}:{password}"
            f"@{settings.postgres_host}:{settings.postgres_port}"
            f"/{settings.postgres_db}"
        )

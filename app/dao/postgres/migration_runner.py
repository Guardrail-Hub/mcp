"""
Hand-written SQL migration runner for the PostgreSQL backend.

No ORM / migration framework (Alembic, etc.) is introduced here — this stays
consistent with the "Persistence via Hand-Written DAO" standard (no
SQLAlchemy models, no query-building layer). It's a minimal, dependency-free
runner: a ``schema_migrations`` tracking table plus a fixed, ordered list of
plain ``.sql`` files, each applied at most once and wrapped in its own
transaction so a partial failure never leaves the schema half-applied.

Startup sequence contract
--------------------------
Required order: Database Ready -> Run Migrations -> Verify required tables ->
Initialize services -> Start HTTP Server -> Accept Requests. Every function
here raises on failure so the caller (``app/core/startup.py``) can abort
startup with a non-zero exit code instead of serving requests against a
missing or stale schema.
"""

from pathlib import Path
from typing import Callable, List

from app.core.mcp_logger import MCPLogger

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_TRACKING_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version     TEXT        PRIMARY KEY,
        applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""

# Tables the application depends on existing before it accepts requests.
REQUIRED_TABLES = ("operations",)

_logger = MCPLogger("PostgresMigrationRunner")


def _migration_files() -> List[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


def run_migrations(connect: Callable[[], "object"]) -> None:
    """Apply every not-yet-applied migration in ``migrations/``, in filename order.

    Args:
        connect: A zero-arg callable returning a new psycopg2 connection
            (``PostgresDAO._connect``) — this module never imports psycopg2
            directly, so it stays importable without the driver installed.

    Raises:
        RuntimeError: If a migration fails to apply. The server must stop
            startup and exit non-zero rather than run against a partially
            migrated schema.
    """
    files = _migration_files()
    if not files:
        _logger.warning("No migration files found under %s", _MIGRATIONS_DIR)
        return

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_TRACKING_TABLE_SQL)

        for path in files:
            version = path.stem
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %(version)s",
                        {"version": version},
                    )
                    if cur.fetchone() is not None:
                        continue

                    _logger.info("Applying migration '%s'", version)
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%(version)s)",
                        {"version": version},
                    )
                    _logger.info("Migration '%s' applied", version)
    except Exception as exc:  # pylint: disable=broad-except
        _logger.error("Migration failed: %s", exc)
        raise RuntimeError(f"PostgreSQL migration failed: {exc}") from exc
    finally:
        conn.close()


def verify_required_tables(connect: Callable[[], "object"]) -> None:
    """Confirm every table the app depends on actually exists.

    Raises:
        RuntimeError: If any required table is missing — never start the API
            with an invalid database schema.
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(%(tables)s)",
                {"tables": list(REQUIRED_TABLES)},
            )
            found = {row[0] for row in cur.fetchall()}
        missing = sorted(set(REQUIRED_TABLES) - found)
        if missing:
            raise RuntimeError(f"Missing required PostgreSQL table(s): {missing}")
        _logger.info("Verified required tables present: %s", list(REQUIRED_TABLES))
    finally:
        conn.close()

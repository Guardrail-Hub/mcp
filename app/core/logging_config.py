"""Runtime logging configuration helpers.

Keeps expected, high-frequency traffic out of the logs without disabling
logging for anything else. Currently this suppresses uvicorn access-log lines
for the ZAP worker heartbeat endpoint, which every worker calls on a fixed
interval and which would otherwise dominate the access log.

Only the ``uvicorn.access`` logger is touched — application logging and all
other API endpoints keep logging normally.
"""

import logging

# Worker heartbeat endpoint — see routers/public/zap_worker_router.py.
_HEARTBEAT_PATH = "/workers/heartbeat"


class HeartbeatAccessLogFilter(logging.Filter):
    """Drop uvicorn access-log records for the worker heartbeat endpoint."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn emits access details via record.args =
        # (client_addr, method, full_path, http_version, status_code).
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            if path.endswith(_HEARTBEAT_PATH):
                return False  # suppress heartbeat access line
        elif _HEARTBEAT_PATH in record.getMessage():
            # Fallback for formatter/version differences.
            return False
        return True  # keep every other request


def configure_access_logging() -> None:
    """Install the heartbeat access-log filter on ``uvicorn.access`` (idempotent)."""
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(f, HeartbeatAccessLogFilter) for f in access_logger.filters):
        return
    access_logger.addFilter(HeartbeatAccessLogFilter())

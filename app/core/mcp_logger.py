"""MCP logger module with timed rotating file and console handler support."""

import os
import sys
from datetime import datetime
import logging as _logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional

from app.core.logger_utils import LoggerUtils


class MCPLogger:
    """Logger class with timed rotating file handler and console output."""

    LOG_FORMAT = (
        "%(asctime)s | %(name)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s"
    )

    def __init__(self, log_name: str = "log", log_level: Optional[str] = None) -> None:
        """Initialize MCPLogger with file and stream handlers.

        Args:
            log_name (str): Base name for the log file and logger instance.
            log_level (Optional[str]): Logging level (e.g., 'INFO', 'DEBUG').
                When omitted, the configured ``LOG_LEVEL`` setting is used so
                DEBUG-level output can be toggled centrally without touching
                call sites. Falls back to 'INFO' if settings are unavailable.
        """
        # Resolve the effective level from settings when the caller did not
        # pin one explicitly. This keeps every logger consistent with the
        # configured LOG_LEVEL, so high-frequency DEBUG logs stay silent in
        # production yet appear the moment LOG_LEVEL=DEBUG is set.
        if log_level is None:
            log_level = self._resolve_configured_level()

        log_dir: str = "logs"

        # Get current date
        now = datetime.now()
        date_str = now.strftime("%Y%m%d")

        # Construct the base path for the log file
        self.log_file_path = os.path.join(log_dir, f"{log_name}_{date_str}.log")

        # 1. Ensure the log directory exists
        os.makedirs(log_dir, exist_ok=True)  # exist_ok avoids a separate existence check

        # 2. Initialize the logger instance
        self.logger = _logging.getLogger(log_name)
        self.logger.setLevel(log_level)

        # Prevent logs from propagating to the root logger
        self.logger.propagate = False

        # 3. Create the Timed Rotating File Handler
        # 'when="midnight"' means rotation occurs at the start of each day.
        # 'interval=1' means rotation happens every 1 unit of 'when' (i.e., every 1 day).
        handler = TimedRotatingFileHandler(
            self.log_file_path,
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
            delay=False,
            utc=True,
            atTime=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        )

        # 4. Set the formatter for the handler
        formatter = _logging.Formatter(self.LOG_FORMAT)
        handler.setFormatter(formatter)

        # 5. Add handlers only once to prevent duplication on re-initialization
        if not self.logger.handlers:
            self.logger.addHandler(handler)

            # 6. Create the Stream Handler to output to console
            stream_handler = _logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)

    @staticmethod
    def _resolve_configured_level() -> str:
        """Return the configured ``LOG_LEVEL``, defaulting to 'INFO'.

        Imported lazily and guarded so logging never becomes a hard dependency
        on settings construction (and to avoid any import cycle during early
        bootstrap). Any failure degrades gracefully to 'INFO'.
        """
        try:
            from app.core.config import settings  # noqa: PLC0415

            return settings.log_level
        except Exception:  # pylint: disable=broad-except
            return "INFO"

    def _log(
        self,
        level: str,
        msg: str,
        *args,
        extra: Optional[dict] = None,  # Optional[dict] instead of a mutable default
        **kwargs,
    ) -> None:
        """Internal logging method that handles both text and JSON logging.

        Args:
            level (str): The logging level.
            msg (str): The message to log.
            *args: Additional arguments to pass to the logging function.
            extra (Optional[dict]): Additional context to log.
            **kwargs: Additional keyword arguments to pass to the logging function.
        """
        log_fn = getattr(self.logger, level.lower())  # resolve the bound level method

        # Write the main message to the local log file and stdout
        log_fn(msg, *args, **kwargs)

        # Log each extra field as a separate line so they are visible in plain-text logs
        LoggerUtils.log_with_extra(
            msg, *args, logger=log_fn, extra=extra, **kwargs
        )

    def info(self, msg: str, *args, extra: Optional[dict] = None, **kwargs) -> None:
        """Log an informational message."""
        self._log("info", msg, *args, extra=extra, **kwargs)

    def error(self, msg: str, *args, extra: Optional[dict] = None, **kwargs) -> None:
        """Log an error message."""
        self._log("error", msg, *args, extra=extra, **kwargs)

    def warning(self, msg: str, *args, extra: Optional[dict] = None, **kwargs) -> None:
        """Log a warning message."""
        self._log("warning", msg, *args, extra=extra, **kwargs)

    def critical(self, msg: str, *args, extra: Optional[dict] = None, **kwargs) -> None:
        """Log a critical message."""
        self._log("critical", msg, *args, extra=extra, **kwargs)

    def debug(self, msg: str, *args, extra: Optional[dict] = None, **kwargs) -> None:
        """Log a debug message."""
        self._log("debug", msg, *args, extra=extra, **kwargs)

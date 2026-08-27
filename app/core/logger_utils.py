"""Low-level logging helper used by :class:`app.core.mcp_logger.MCPLogger`.

Lives in ``core/`` (not a separate ``utils/`` role) because it is a
foundational logging concern that ``core/mcp_logger`` depends on. Keeping it
here preserves the one-directional dependency rule: ``core/`` never imports
from a higher layer.
"""

from urllib.parse import urlsplit, urlunsplit


class LoggerUtils:
    """Static helpers for emitting structured ``extra`` fields to plain-text logs."""

    @staticmethod
    def redact_url_credentials(url: str) -> str:
        """Return *url* with any embedded ``user:password@`` removed.

        Guardrail G1 forbids a credential reaching a log line, and userinfo is
        the one place a URL structurally holds one — so it is the one part
        stripped. The scheme, host, path and query are kept deliberately: they
        are what make the line answer "which endpoint was this", and a URL
        redacted down to its host identifies nothing.

        Lives here rather than beside either caller because two of them exist
        (the JMeter worker's pre-launch line and the server's dispatch line),
        they sit in different layers, and a second copy is how the two would
        drift into redacting different things.

        Args:
            url (str): The URL about to be logged.

        Returns:
            str: The same URL when it carries no userinfo; otherwise the URL
            with the userinfo component dropped.
        """
        parsed = urlsplit(url)
        if parsed.username is None and parsed.password is None:
            return url
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )

    @staticmethod
    def log_with_extra(msg, *args, logger, extra=None, **kwargs):
        """Log *msg*, then one line per key/value pair in *extra*.

        Used when ``extra`` fields need to be visible in plain-text local log
        files, since the standard logging formatter does not automatically
        print the contents of an ``extra`` dict.

        Args:
            msg (str): The primary log message.
            *args: Positional format arguments forwarded to each logger call
                (e.g. ``"val: %s", val``).
            logger (callable): The bound logger method to write with
                (e.g. ``logger.info``, ``logger.error``).
            extra (dict | None): Structured key/value fields to log
                individually after the main message. Defaults to None.
            **kwargs: Additional keyword arguments forwarded to each logger
                call (e.g. ``exc_info``, ``stack_info``).

        Returns:
            None
        """
        # Main message first, then one line per extra key.
        if extra:
            for key, value in extra.items():
                logger(f"{key}: {value}", *args, **kwargs)

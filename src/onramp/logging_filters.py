"""Privacy-safe formatting for the standard Uvicorn HTTP access logger.

Only the logging record is changed: routing, query parsing, and response URLs
remain untouched. Upstream proxies and custom application loggers need their
own redaction policies.
"""

import logging
import threading


MAX_LOGGED_REQUEST_PATH = 2048
QUERY_REDACTION = "?[redacted]"
_installation_lock = threading.Lock()


class AccessQueryRedactionFilter(logging.Filter):
    """Remove every query value, not just a brittle list of secret parameters."""

    _onramp_access_query_redaction = True

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn h11 and httptools both use this five-argument access shape.
        # Do not reinterpret unrelated records from other application loggers.
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) == 5
            and isinstance(record.args[2], str)
        ):
            target = record.args[2]
            bounded_target = target[:MAX_LOGGED_REQUEST_PATH]
            path, separator, _query = bounded_target.partition("?")
            if separator:
                path += QUERY_REDACTION
            elif len(target) > MAX_LOGGED_REQUEST_PATH:
                path += "[truncated]"
            record.args = (*record.args[:2], path, *record.args[3:])
        return True


def install_access_log_redaction() -> None:
    """Install after Uvicorn configures logging; repeated app creation is safe."""
    logger = logging.getLogger("uvicorn.access")
    with _installation_lock:
        if not any(
            getattr(filter_, "_onramp_access_query_redaction", False)
            for filter_ in logger.filters
        ):
            logger.addFilter(AccessQueryRedactionFilter())

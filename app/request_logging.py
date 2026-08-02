"""Reduce routine polling log noise without hiding failures or slow requests."""

from __future__ import annotations

import logging
from typing import Any

# These endpoints are requested repeatedly by the browser, health probes, or both.
# Successful fast reads provide little operational value at INFO level, while errors
# and slow responses remain visible.
QUIET_SUCCESS_PATHS = frozenset(
    {
        "/health",
        "/health/live",
        "/health/ready",
        "/v1/events",
        "/v1/models/status",
        "/v1/system/status",
        "/v1/system/telemetry",
    }
)
SLOW_REQUEST_THRESHOLD_MS = 500.0
_REQUEST_LOG_TEMPLATE = "HTTP %s %s - Status: %d - Latency: %.2fms"
_QUIET_METHODS = frozenset({"GET", "HEAD"})
_RequestLogFields = tuple[str, str, int, float]


def _request_log_fields(record: logging.LogRecord) -> _RequestLogFields | None:
    """Parse the server's structured successful-request log record."""

    if record.levelno != logging.INFO or record.msg != _REQUEST_LOG_TEMPLATE:
        return None
    args: Any = record.args
    if not isinstance(args, tuple) or len(args) != 4:
        return None
    method, path, status_code, duration_ms = args
    try:
        return str(method).upper(), str(path), int(status_code), float(duration_ms)
    except (TypeError, ValueError, OverflowError):
        return None


class PollingRequestLogFilter(logging.Filter):
    """Demote fast successful polling requests while preserving useful signals."""

    def __init__(self, *, slow_request_ms: float = SLOW_REQUEST_THRESHOLD_MS) -> None:
        super().__init__()
        self.slow_request_ms = max(float(slow_request_ms), 0.0)

    def filter(self, record: logging.LogRecord) -> bool:
        fields = _request_log_fields(record)
        if fields is None:
            return True

        method, path, status_code, duration_ms = fields
        quiet_success = (
            method in _QUIET_METHODS
            and path in QUIET_SUCCESS_PATHS
            and 200 <= status_code < 400
            and duration_ms < self.slow_request_ms
        )
        if not quiet_success:
            return True

        # Preserve the record for explicit DEBUG sessions without emitting it during
        # normal INFO-level desktop operation. A child logger avoids re-entering this
        # filter when DEBUG is enabled.
        logging.getLogger(f"{record.name}.polling").debug(record.getMessage())
        return False


def install_request_log_filter(
    logger: logging.Logger | None = None,
    *,
    slow_request_ms: float = SLOW_REQUEST_THRESHOLD_MS,
) -> PollingRequestLogFilter:
    """Install the filter once and return the active instance."""

    target = logger or logging.getLogger("ov-llm.server")
    for current in target.filters:
        if isinstance(current, PollingRequestLogFilter):
            return current
    installed = PollingRequestLogFilter(slow_request_ms=slow_request_ms)
    target.addFilter(installed)
    return installed


__all__ = [
    "PollingRequestLogFilter",
    "QUIET_SUCCESS_PATHS",
    "SLOW_REQUEST_THRESHOLD_MS",
    "install_request_log_filter",
]

import logging

from app.request_logging import PollingRequestLogFilter, install_request_log_filter


def _request_record(method="GET", path="/health", status=200, duration_ms=10.0):
    return logging.LogRecord(
        name="ov-llm.server",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP %s %s - Status: %d - Latency: %.2fms",
        args=(method, path, status, duration_ms),
        exc_info=None,
    )


def test_fast_successful_polling_requests_are_demoted():
    request_filter = PollingRequestLogFilter(slow_request_ms=500)

    assert request_filter.filter(_request_record(path="/health")) is False
    assert request_filter.filter(_request_record(path="/v1/models/status")) is False
    assert request_filter.filter(_request_record(path="/v1/system/telemetry")) is False
    assert request_filter.filter(_request_record(path="/v1/events")) is False


def test_polling_paths_are_normalized_before_matching():
    request_filter = PollingRequestLogFilter(slow_request_ms=500)

    assert request_filter.filter(_request_record(path="/health/")) is False
    assert (
        request_filter.filter(_request_record(path="/v1/events?cursor=17&limit=50")) is False
    )
    assert (
        request_filter.filter(_request_record(path="http://127.0.0.1/v1/models/status?x=1"))
        is False
    )
    assert request_filter.filter(_request_record(path="/v1/events-extra?cursor=17")) is True


def test_failures_slow_requests_and_non_polling_routes_remain_visible():
    request_filter = PollingRequestLogFilter(slow_request_ms=500)

    assert request_filter.filter(_request_record(path="/health/ready", status=503)) is True
    assert request_filter.filter(_request_record(path="/health", duration_ms=500)) is True
    assert request_filter.filter(_request_record(path="/v1/chat/completions")) is True
    assert request_filter.filter(_request_record(method="POST", path="/v1/models/status")) is True


def test_unrelated_log_records_are_unchanged():
    request_filter = PollingRequestLogFilter()
    record = logging.LogRecord(
        name="ov-llm.server",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Loaded %s",
        args=("model",),
        exc_info=None,
    )

    assert request_filter.filter(record) is True


def test_installation_is_idempotent():
    logger = logging.getLogger("inferbridge-test-request-logging")
    logger.filters.clear()

    first = install_request_log_filter(logger)
    second = install_request_log_filter(logger)

    assert first is second
    assert sum(isinstance(item, PollingRequestLogFilter) for item in logger.filters) == 1

import logging

import pytest

from utils.logger_config import (
    HTTPX_SUPPRESSED_ON_SUCCESS_PATTERNS,
    RateLimitedEndpointFilter,
    create_httpx_filter,
    install_httpx_filter,
)

# Same format string httpx 0.28 uses in Client.send / AsyncClient.send.
_HTTPX_FORMAT = 'HTTP Request: %s %s "%s %d %s"'
_AVAILABILITY_URL = "http://127.0.0.1:48912/internal/knowledge/availability"
_LATEST_VERSIONS_URL = (
    "https://market.project-neko.cn/api/v1/plugins/latest-versions?ids=1,2"
)


def _httpx_record(url, status_code, reason="OK", level=logging.INFO, args=None):
    if args is None:
        args = ("GET", url, "HTTP/1.1", status_code, reason)
    return logging.LogRecord(
        name="httpx",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=_HTTPX_FORMAT,
        args=args,
        exc_info=None,
    )


@pytest.mark.unit
@pytest.mark.parametrize("url", [_AVAILABILITY_URL, _LATEST_VERSIONS_URL])
@pytest.mark.parametrize("status_code", [200, 204, 299])
def test_success_only_patterns_hide_2xx(url, status_code):
    assert create_httpx_filter().filter(_httpx_record(url, status_code)) is False


@pytest.mark.unit
@pytest.mark.parametrize("url", [_AVAILABILITY_URL, _LATEST_VERSIONS_URL])
@pytest.mark.parametrize("status_code", [301, 404, 500, 503])
def test_success_only_patterns_keep_non_2xx(url, status_code):
    record = _httpx_record(url, status_code, reason="Error")
    assert create_httpx_filter().filter(record) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        ("GET", _AVAILABILITY_URL, "HTTP/1.1", "200", "OK"),  # status not an int
        ("GET", _AVAILABILITY_URL, "HTTP/1.1", True, "OK"),  # bool is not a status
        ("GET", _AVAILABILITY_URL, "HTTP/1.1", 200),  # unexpected arity
    ],
)
def test_success_only_patterns_keep_records_without_readable_status(args):
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request:" + " %s" * len(args),
        args=None,
        exc_info=None,
    )
    # Set args after construction: LogRecord unwraps a single mapping arg.
    record.args = args
    assert create_httpx_filter().filter(record) is True


@pytest.mark.unit
def test_success_only_patterns_keep_records_without_args():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=f"HTTP Request: GET {_AVAILABILITY_URL} 200 OK",
        args=None,
        exc_info=None,
    )
    assert create_httpx_filter().filter(record) is True


@pytest.mark.unit
@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR, logging.CRITICAL])
def test_warning_and_above_always_pass(level):
    record = _httpx_record(_AVAILABILITY_URL, 200, level=level)
    assert create_httpx_filter().filter(record) is True


@pytest.mark.unit
def test_unrelated_success_requests_are_kept():
    record = _httpx_record("http://127.0.0.1:48911/api/public-knowledge/status", 200)
    assert create_httpx_filter().filter(record) is True


@pytest.mark.unit
def test_full_suppression_still_ignores_status():
    # Existing patterns keep their status-agnostic semantics.
    record = _httpx_record("http://127.0.0.1:48915/mcp/availability", 500, reason="Error")
    assert create_httpx_filter().filter(record) is False


@pytest.mark.unit
def test_success_only_list_defaults_to_empty():
    flt = RateLimitedEndpointFilter(suppressed_endpoints=[])
    assert flt.suppressed_on_success_endpoints == []
    assert flt.filter(_httpx_record(_AVAILABILITY_URL, 200)) is True


@pytest.mark.unit
def test_success_only_patterns_cover_heartbeat_and_update_check():
    assert "/internal/knowledge/availability" in HTTPX_SUPPRESSED_ON_SUCCESS_PATTERNS
    assert "/api/v1/plugins/latest-versions" in HTTPX_SUPPRESSED_ON_SUCCESS_PATTERNS


@pytest.mark.unit
def test_install_httpx_filter_is_idempotent(monkeypatch):
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "filters", [])
    # setattr (not delattr) so the marker is removed again on undo when absent.
    monkeypatch.setattr(httpx_logger, "_neko_httpx_filter_installed", False, raising=False)

    install_httpx_filter()
    install_httpx_filter()

    installed = [f for f in httpx_logger.filters if isinstance(f, RateLimitedEndpointFilter)]
    assert len(installed) == 1

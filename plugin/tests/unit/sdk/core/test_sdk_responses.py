from __future__ import annotations

import pytest

from plugin._types.errors import ErrorCode
from plugin.sdk.responses import fail, is_envelope, ok


@pytest.mark.plugin_unit
def test_ok_includes_meta_and_custom_time() -> None:
    payload = ok(
        data={"x": 1},
        message="done",
        trace_id="t1",
        time="2026-03-07T00:00:00Z",
        request_id="r1",
    )
    assert payload["success"] is True
    assert payload["code"] == int(ErrorCode.SUCCESS)
    assert payload["data"] == {"x": 1}
    assert payload["message"] == "done"
    assert payload["error"] is None
    assert payload["trace_id"] == "t1"
    assert payload["time"] == "2026-03-07T00:00:00Z"
    assert payload["meta"]["request_id"] == "r1"
    assert is_envelope(payload) is True


@pytest.mark.plugin_unit
def test_fail_with_errorcode_and_int_code_paths() -> None:
    payload_enum = fail(ErrorCode.VALIDATION_ERROR, "bad request", retriable=True)
    assert payload_enum["success"] is False
    assert payload_enum["code"] == int(ErrorCode.VALIDATION_ERROR)
    assert payload_enum["error"]["code"] == "VALIDATION_ERROR"
    assert payload_enum["error"]["retriable"] is True
    assert is_envelope(payload_enum) is True

    payload_int = fail(404, "not found")
    assert payload_int["success"] is False
    assert payload_int["code"] == 404
    assert payload_int["error"]["code"] == "NOT_FOUND"
    assert payload_int["error"]["message"] == "not found"
    assert is_envelope(payload_int) is True


@pytest.mark.plugin_unit
def test_fail_with_string_code_defaults_to_internal_and_meta() -> None:
    payload = fail("CUSTOM_ERR", "failed", details={"k": "v"}, retriable=True, request_id="req-1")
    assert payload["success"] is False
    assert payload["code"] == int(ErrorCode.INTERNAL)
    assert payload["error"]["code"] == "CUSTOM_ERR"
    assert payload["error"]["message"] == "failed"
    assert payload["error"]["details"] == {"k": "v"}
    assert payload["error"]["retriable"] is True
    assert payload["meta"]["request_id"] == "req-1"
    assert is_envelope(payload) is True


@pytest.mark.plugin_unit
def test_is_envelope_valid_and_invalid_shapes() -> None:
    valid_payload = ok(data={"a": 1})
    assert is_envelope(valid_payload) is True

    invalid_values = [
        None,
        "x",
        {"success": True},
        {"success": True, "error": None},
        {"success": "yes", "error": None, "time": "2026-03-07T00:00:00Z"},
    ]
    for value in invalid_values:
        assert is_envelope(value) is False

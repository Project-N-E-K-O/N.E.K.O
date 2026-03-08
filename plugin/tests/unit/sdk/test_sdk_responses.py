from __future__ import annotations

import pytest

from plugin._types.errors import ErrorCode
from plugin.sdk.responses import fail, is_envelope, ok


@pytest.mark.plugin_unit
def test_ok_response_shape() -> None:
    payload = ok(data={"x": 1}, message="done", trace_id="t1", request_id="r1")
    assert payload["success"] is True
    assert payload["code"] == int(ErrorCode.SUCCESS)
    assert payload["data"] == {"x": 1}
    assert payload["error"] is None
    assert payload["trace_id"] == "t1"
    assert payload["meta"]["request_id"] == "r1"
    assert is_envelope(payload) is True


@pytest.mark.plugin_unit
def test_fail_response_with_enum_code() -> None:
    payload = fail(ErrorCode.VALIDATION_ERROR, "bad request", retriable=True)
    assert payload["success"] is False
    assert payload["code"] == int(ErrorCode.VALIDATION_ERROR)
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["retriable"] is True
    assert is_envelope(payload) is True


@pytest.mark.plugin_unit
def test_fail_response_with_custom_string_code_uses_internal_numeric_code() -> None:
    payload = fail("CUSTOM_ERR", "failed")
    assert payload["code"] == int(ErrorCode.INTERNAL)
    assert payload["error"]["code"] == "CUSTOM_ERR"


@pytest.mark.plugin_unit
@pytest.mark.parametrize("value", [None, "x", {"success": True}, {"success": True, "error": None}])
def test_is_envelope_rejects_invalid_values(value: object) -> None:
    assert is_envelope(value) is False

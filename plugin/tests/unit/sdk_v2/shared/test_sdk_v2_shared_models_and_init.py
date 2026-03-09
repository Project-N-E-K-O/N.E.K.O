from __future__ import annotations

import importlib

from plugin.sdk_v2 import shared
from plugin.sdk_v2.shared import models
from plugin.sdk_v2.shared import constants
from plugin.sdk_v2.shared.models import errors, responses


def test_shared_and_models_exports() -> None:
    shared_mod = importlib.reload(shared)
    models_mod = importlib.reload(models)

    for name in shared_mod.__all__:
        assert hasattr(shared_mod, name)

    for name in models_mod.__all__:
        assert hasattr(models_mod, name)


def test_error_code_values() -> None:
    assert int(errors.ErrorCode.SUCCESS) == 0
    assert int(errors.ErrorCode.VALIDATION_ERROR) == 400
    assert int(errors.ErrorCode.NOT_FOUND) == 404
    assert int(errors.ErrorCode.TIMEOUT) == 408
    assert int(errors.ErrorCode.CONFLICT) == 409
    assert int(errors.ErrorCode.INTERNAL) == 500


def test_responses_helpers_and_version() -> None:
    ok_envelope = responses.ok(data={"x": 1}, trace_id="t", source="unit")
    assert ok_envelope["success"] is True
    assert ok_envelope["meta"]["source"] == "unit"

    err_enum = responses.fail(errors.ErrorCode.NOT_FOUND, "missing", details={"id": "1"})
    assert err_enum["success"] is False
    assert err_enum["code"] == 404
    assert err_enum["error"]["code"] == "NOT_FOUND"

    err_int_known = responses.fail(409, "conflict")
    assert err_int_known["error"]["code"] == "CONFLICT"

    err_int_unknown = responses.fail(499, "x")
    assert err_int_unknown["error"]["code"] == "499"

    err_str = responses.fail("E_CUSTOM", "bad")
    assert err_str["code"] == int(errors.ErrorCode.INTERNAL)
    assert err_str["error"]["code"] == "E_CUSTOM"
    err_with_meta = responses.fail("E_META", "bad", source="unit")
    assert err_with_meta["meta"]["source"] == "unit"

    assert responses.is_envelope(ok_envelope) is True
    assert responses.is_envelope(err_enum) is True
    assert responses.is_envelope({"success": True}) is False

    assert constants.SDK_VERSION == "0.1.0"

from __future__ import annotations

import pytest

from plugin.sdk_v2.shared.models import (
    Err,
    Ok,
    ResultError,
    bind_result,
    capture,
    is_err,
    is_ok,
    map_err_result,
    map_result,
    match_result,
    must,
    unwrap,
    unwrap_or,
)


@pytest.mark.plugin_unit
def test_result_ok_err_and_unwrap_helpers() -> None:
    r1 = Ok(3)
    r2 = Err({"error": {"code": "BAD", "message": "bad", "details": {"x": 1}}})

    assert is_ok(r1) is True
    assert is_err(r1) is False
    assert is_ok(r2) is False
    assert is_err(r2) is True

    assert unwrap(r1) == 3
    assert unwrap_or(r1, 0) == 3
    assert unwrap_or(r2, 7) == 7

    with pytest.raises(ResultError) as ei:
        unwrap(r2)
    assert ei.value.code == "BAD"
    assert ei.value.details == {"x": 1}


@pytest.mark.plugin_unit
def test_result_map_bind_and_match_result() -> None:
    r = Ok(2)
    r2 = map_result(r, lambda v: v + 1)
    assert isinstance(r2, Ok)
    assert r2.value == 3

    r3 = bind_result(r2, lambda v: Ok(v * 4))
    assert isinstance(r3, Ok)
    assert r3.value == 12

    r4 = bind_result(r3, lambda _v: Err("boom"))
    assert isinstance(r4, Err)

    out = match_result(r4, on_ok=lambda v: f"ok:{v}", on_err=lambda e: f"err:{e}")
    assert out == "err:boom"


@pytest.mark.plugin_unit
def test_result_bind_supports_mixed_error_types() -> None:
    class HttpError(Exception):
        pass

    class ValidationError(Exception):
        pass

    r0 = Err(HttpError("http"))
    r1 = bind_result(r0, lambda _v: Err(ValidationError("validation")))
    assert isinstance(r1, Err)
    assert isinstance(r1.error, HttpError)

    r2 = bind_result(Ok(1), lambda _v: Err(ValidationError("validation")))
    assert isinstance(r2, Err)
    assert isinstance(r2.error, ValidationError)


@pytest.mark.plugin_unit
def test_result_map_err_and_capture_helpers() -> None:
    r1 = Ok(3).map_err(lambda e: f"x:{e}")
    assert isinstance(r1, Ok)
    assert r1.value == 3

    r2 = Err("boom").map_err(lambda e: f"sdk:{e}")
    assert isinstance(r2, Err)
    assert r2.error == "sdk:boom"

    r3 = map_err_result(Err(123), lambda e: f"E{e}")
    assert isinstance(r3, Err)
    assert r3.error == "E123"

    ok_captured = capture(lambda: 42)
    assert isinstance(ok_captured, Ok)
    assert ok_captured.value == 42

    err_captured = capture(lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert isinstance(err_captured, Err)
    assert isinstance(err_captured.error, ValueError)


@pytest.mark.plugin_unit
def test_result_try_except_and_match_pattern() -> None:
    def _flow(x: int):
        if x > 0:
            return Ok(x)
        return Err("negative")

    with pytest.raises(ResultError):
        must(_flow(-1))

    assert must(_flow(5)) == 5

    m = _flow(8)
    label = ""
    match m:
        case Ok(v):
            label = f"ok:{v}"
        case Err(e):
            label = f"err:{e}"
    assert label == "ok:8"


@pytest.mark.plugin_unit
def test_result_err_exception_passthrough_and_non_exception_wrapping() -> None:
    with pytest.raises(ValueError):
        must(Err(ValueError("bad input")))

    with pytest.raises(ResultError):
        must(Err({"error": {"code": "BAD", "message": "bad"}}))

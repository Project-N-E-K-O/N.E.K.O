from __future__ import annotations

import pytest

from plugin.sdk_v2.shared.models.result import (
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
    raise_for_err,
    unwrap,
    unwrap_or,
)


def test_result_error_message_and_payload_shapes() -> None:
    e_nested = ResultError({"error": {"code": "BAD", "message": "bad", "details": {"x": 1}}})
    assert e_nested.code == "BAD"
    assert e_nested.details == {"x": 1}
    assert str(e_nested) == "bad"

    e_flat = ResultError({"code": "E1", "message": "m1", "details": 2})
    assert e_flat.code == "E1"
    assert e_flat.details == 2
    assert str(e_flat) == "m1"

    e_scalar_nested = ResultError({"code": "E2", "details": {"y": 2}, "error": "flat message"})
    assert e_scalar_nested.code == "E2"
    assert e_scalar_nested.details == {"y": 2}
    assert str(e_scalar_nested) == "flat message"

    e_scalar = ResultError("boom")
    assert str(e_scalar) == "boom"

    e_override = ResultError("ignored", message="fixed")
    assert str(e_override) == "fixed"


def test_ok_methods_and_transformations() -> None:
    ok_result = Ok(3)
    assert ok_result.is_ok() is True
    assert ok_result.is_err() is False
    assert ok_result.value_or_none() == 3
    assert ok_result.err() is None
    assert ok_result.map(lambda value: value + 1) == Ok(4)
    assert ok_result.map_err(lambda value: f"{value}") is ok_result
    assert ok_result.bind(lambda value: Ok(value * 2)) == Ok(6)
    assert ok_result.unwrap() == 3
    assert ok_result.unwrap_or(9) == 3
    assert ok_result.raise_for_err() is None


def test_err_methods_and_transformations() -> None:
    err_result = Err("x")
    assert err_result.is_ok() is False
    assert err_result.is_err() is True
    assert err_result.value_or_none() is None
    assert err_result.err() == "x"
    assert err_result.map(lambda value: value + 1) is err_result
    mapped = err_result.map_err(lambda value: f"E:{value}")
    assert mapped == Err("E:x")
    assert err_result.bind(lambda value: Ok(value)) is err_result
    assert err_result.unwrap_or(5) == 5


def test_err_unwrap_and_raise_for_err_behaviors() -> None:
    err_with_exc = Err(ValueError("bad"))
    with pytest.raises(ValueError):
        err_with_exc.unwrap()
    with pytest.raises(ValueError):
        err_with_exc.raise_for_err()

    err_plain = Err({"error": {"code": "BAD", "message": "bad"}})
    with pytest.raises(ResultError):
        err_plain.unwrap()
    with pytest.raises(ResultError):
        err_plain.raise_for_err()


def test_function_helpers_on_ok_and_err() -> None:
    ok_result = Ok(2)
    err_result = Err("bad")

    assert is_ok(ok_result) is True
    assert is_err(ok_result) is False
    assert is_ok(err_result) is False
    assert is_err(err_result) is True

    assert map_result(ok_result, lambda value: value + 2) == Ok(4)
    assert map_result(err_result, lambda value: value + 2) is err_result

    assert map_err_result(ok_result, lambda value: f"x:{value}") is ok_result
    assert map_err_result(err_result, lambda value: f"x:{value}") == Err("x:bad")

    assert bind_result(ok_result, lambda value: Ok(value * 3)) == Ok(6)
    assert bind_result(err_result, lambda value: Ok(value * 3)) is err_result

    assert unwrap(ok_result) == 2
    assert unwrap_or(ok_result, 9) == 2
    assert unwrap_or(err_result, 9) == 9

    assert raise_for_err(ok_result) is None
    with pytest.raises(ResultError):
        raise_for_err(err_result)


def test_unwrap_must_and_match_result() -> None:
    ok_result = Ok(8)
    err_result = Err("boom")

    assert must(ok_result) == 8
    with pytest.raises(ResultError):
        must(err_result)

    assert match_result(ok_result, on_ok=lambda value: f"success:{value}", on_err=lambda value: f"err:{value}") == "success:8"
    assert match_result(err_result, on_ok=lambda value: f"success:{value}", on_err=lambda value: f"err:{value}") == "err:boom"

    match ok_result:
        case Ok(value):
            label = f"success:{value}"
        case Err(error):
            label = f"err:{error}"
    assert label == "success:8"


def test_exception_passthrough_in_helpers() -> None:
    err_result = Err(ValueError("bad"))
    with pytest.raises(ValueError):
        unwrap(err_result)
    with pytest.raises(ValueError):
        raise_for_err(err_result)
    with pytest.raises(ValueError):
        must(err_result)


def test_capture_wrapper_success_and_error() -> None:
    assert capture(lambda: 42) == Ok(42)

    captured = capture(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert isinstance(captured, Err)
    assert isinstance(captured.error, RuntimeError)

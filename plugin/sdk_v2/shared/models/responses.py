"""SDK v2 response envelope helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeAlias

from .errors import ErrorCode

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ok(
    data: JsonValue | JsonObject | None = None,
    *,
    code: ErrorCode = ErrorCode.SUCCESS,
    message: str = "",
    trace_id: str | None = None,
    **meta: JsonValue,
) -> JsonObject:
    payload: JsonObject = {
        "success": True,
        "code": int(code),
        "data": data,
        "message": message,
        "error": None,
        "time": _now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def fail(
    code: ErrorCode | str | int,
    message: str,
    *,
    details: JsonValue | JsonObject | None = None,
    retriable: bool = False,
    trace_id: str | None = None,
    **meta: JsonValue,
) -> JsonObject:
    if isinstance(code, ErrorCode):
        code_int = int(code)
        code_name = code.name
    elif isinstance(code, int):
        code_int = code
        try:
            code_name = ErrorCode(code).name
        except ValueError:
            code_name = str(code)
    else:
        code_int = int(ErrorCode.INTERNAL)
        code_name = str(code)

    payload: JsonObject = {
        "success": False,
        "code": code_int,
        "data": None,
        "message": "",
        "error": {
            "code": code_name,
            "message": message,
            "details": details,
            "retriable": retriable,
        },
        "time": _now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def is_envelope(value: object) -> bool:
    return isinstance(value, dict) and value.get("success") in (True, False) and "error" in value and "time" in value


__all__ = ["ok", "fail", "is_envelope"]

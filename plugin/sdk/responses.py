from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from .errors import ErrorCode


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_iso() -> str:
    return _now_iso()


def ok(
    data: Any = None,
    *,
    message: str = "",
    trace_id: Optional[str] = None,
    time: Optional[str] = None,
    **meta: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": True,
        "data": data,
        "message": message,
        "error": None,
        "time": time or _now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def fail(
    code: Union[ErrorCode, str],
    message: str,
    *,
    details: Any = None,
    retriable: bool = False,
    trace_id: Optional[str] = None,
    time: Optional[str] = None,
    **meta: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "data": None,
        "message": "",
        "error": {
            "code": str(code),
            "message": message,
            "details": details,
            "retriable": retriable,
        },
        "time": time or _now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def is_envelope(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("success") not in (True, False):
        return False
    if "error" not in value:
        return False
    if "time" not in value:
        return False
    return True

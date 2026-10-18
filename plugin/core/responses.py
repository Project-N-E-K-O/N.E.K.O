from __future__ import annotations

from typing import Any, Dict, Optional, Union

from plugin._types.errors import ErrorCode, get_error_name
from plugin.utils.time_utils import now_iso


def ok(
    data: Any = None,
    *,
    code: ErrorCode = ErrorCode.SUCCESS,
    message: str = "",
    trace_id: Optional[str] = None,
    time: Optional[str] = None,
    **meta: Any,
) -> Dict[str, Any]:
    """Create the standard success-response format
    
    Plugin entries use this to return success with a consistent success flag, data, and metadata.
    
    Args:
        data: Returned data, of any serializable type
        code: Error code, default SUCCESS (0)
        message: Optional explanatory message
        trace_id: Optional trace ID for log correlation
        time: Optional timestamp, defaulting to current UTC
        **meta: Extra metadata placed in the \"meta\" field
    
    Returns:
        Standard response dictionary with this shape:
        {
            \"success\": True,
            \"code\": 0,
            \"data\": <data>,
            \"message\": <message>,
            \"error\": None,
            \"time\": <ISO timestamp>,
            \"trace_id\": <trace_id>,
            \"meta\": <meta>  # If meta was supplied
        }
    
    Example:
        >>> ok(data={\"result\": 42})
        {'success': True, 'code': 0, 'data': {'result': 42}, ...}
        
        >>> ok(data=[1, 2, 3], message=\"处理完成\", custom_field=\"value\")
        {'success': True, 'code': 0, 'data': [1, 2, 3], 'message': '处理完成', 'meta': {'custom_field': 'value'}, ...}
    """
    payload: Dict[str, Any] = {
        "success": True,
        "code": int(code),
        "data": data,
        "message": message,
        "error": None,
        "time": time or now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def fail(
    code: Union[ErrorCode, str, int],
    message: str,
    *,
    details: Any = None,
    retriable: bool = False,
    trace_id: Optional[str] = None,
    time: Optional[str] = None,
    **meta: Any,
) -> Dict[str, Any]:
    """Create the standard failure-response format
    
    Plugin entries use this to return consistent errors containing code, message, and details.
    
    Args:
        code: Error code as ErrorCode enum, integer, or custom string
        message: Error description
        details: Optional error details, of any serializable type
        retriable: Whether the client may retry this operation
        trace_id: Optional trace ID for log correlation
        time: Optional timestamp, defaulting to current UTC
        **meta: Extra metadata placed in the \"meta\" field
    
    Returns:
        Standard error-response dictionary with this shape:
        {
            \"success\": False,
            \"code\": <int>,
            \"data\": None,
            \"message\": \"\",
            \"error\": {
                \"code\": <code_name>,
                \"message\": <message>,
                \"details\": <details>,
                \"retriable\": <retriable>
            },
            \"time\": <ISO timestamp>,
            \"trace_id\": <trace_id>,
            \"meta\": <meta>  # If meta was supplied
        }
    
    Example:
        >>> from plugin.sdk import ErrorCode, fail
        >>> fail(ErrorCode.VALIDATION_ERROR, \"参数无效\")
        {'success': False, 'code': 400, 'error': {'code': 'VALIDATION_ERROR', 'message': '参数无效', ...}, ...}
        
        >>> fail(\"CUSTOM_ERROR\", \"自定义错误\", details={\"field\": \"email\"}, retriable=True)
        {'success': False, 'code': 500, 'error': {'code': 'CUSTOM_ERROR', 'retriable': True, ...}, ...}
    """
    # Parse error codes
    if isinstance(code, ErrorCode):
        code_int = int(code)
        code_name = get_error_name(code)
    elif isinstance(code, int):
        code_int = code
        try:
            code_name = get_error_name(ErrorCode(code))
        except ValueError:
            code_name = str(code)
    else:
        code_int = ErrorCode.INTERNAL.value  # Custom strings default to 500
        code_name = str(code)
    
    payload: Dict[str, Any] = {
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
        "time": time or now_iso(),
        "trace_id": trace_id,
    }
    if meta:
        payload["meta"] = meta
    return payload


def is_envelope(value: Any) -> bool:
    """Check whether a value uses the standard response format
    
    Validate agreement with the standard ok()/fail() response shape.
    
    Args:
        value: Value to inspect
    
    Returns:
        True for standard response format, otherwise False
    
    Example:
        >>> is_envelope(ok(data=\"test\"))
        True
        >>> is_envelope({\"success\": True})
        False  # Missing required fields
        >>> is_envelope(\"not a dict\")
        False
    """
    if not isinstance(value, dict):
        return False
    if value.get("success") not in (True, False):
        return False
    if "error" not in value:
        return False
    if "time" not in value:
        return False
    return True

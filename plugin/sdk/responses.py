from __future__ import annotations

from typing import Any, Dict, Optional, Union

from .errors import ErrorCode, get_error_name
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
    """创建成功响应的标准格式
    
    用于插件entry返回成功结果。返回统一的响应格式,包含成功标记、数据和元信息。
    
    Args:
        data: 返回的数据,可以是任何可序列化的类型
        code: 错误码,默认 SUCCESS (0)
        message: 可选的消息说明
        trace_id: 可选的追踪ID,用于日志关联
        time: 可选的时间戳,默认使用当前UTC时间
        **meta: 额外的元数据,会放入"meta"字段
    
    Returns:
        标准响应字典,格式为:
        {
            "success": True,
            "code": 0,
            "data": <data>,
            "message": <message>,
            "error": None,
            "time": <ISO时间>,
            "trace_id": <trace_id>,
            "meta": <meta>  # 如果提供了meta
        }
    
    Example:
        >>> ok(data={"result": 42})
        {'success': True, 'code': 0, 'data': {'result': 42}, ...}
        
        >>> ok(data=[1, 2, 3], message="处理完成", custom_field="value")
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
    """创建失败响应的标准格式
    
    用于插件entry返回错误结果。返回统一的错误响应格式,包含错误码、错误信息和详情。
    
    Args:
        code: 错误码,可以使用ErrorCode枚举、整数或自定义字符串
        message: 错误描述信息
        details: 可选的错误详情,可以是任何可序列化的类型
        retriable: 是否可重试,True表示客户端可以重试此操作
        trace_id: 可选的追踪ID,用于日志关联
        time: 可选的时间戳,默认使用当前UTC时间
        **meta: 额外的元数据,会放入"meta"字段
    
    Returns:
        标准错误响应字典,格式为:
        {
            "success": False,
            "code": <int>,
            "data": None,
            "message": "",
            "error": {
                "code": <code_name>,
                "message": <message>,
                "details": <details>,
                "retriable": <retriable>
            },
            "time": <ISO时间>,
            "trace_id": <trace_id>,
            "meta": <meta>  # 如果提供了meta
        }
    
    Example:
        >>> from plugin.sdk import ErrorCode, fail
        >>> fail(ErrorCode.VALIDATION_ERROR, "参数无效")
        {'success': False, 'code': 400, 'error': {'code': 'VALIDATION_ERROR', 'message': '参数无效', ...}, ...}
        
        >>> fail("CUSTOM_ERROR", "自定义错误", details={"field": "email"}, retriable=True)
        {'success': False, 'code': 500, 'error': {'code': 'CUSTOM_ERROR', 'retriable': True, ...}, ...}
    """
    # 解析错误码
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
        code_int = ErrorCode.INTERNAL.value  # 自定义字符串默认使用 500
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
    """检查值是否是标准响应格式
    
    验证给定的值是否符合ok()/fail()返回的标准响应格式。
    
    Args:
        value: 要检查的值
    
    Returns:
        True 如果是标准响应格式,False 否则
    
    Example:
        >>> is_envelope(ok(data="test"))
        True
        >>> is_envelope({"success": True})
        False  # 缺少必需字段
        >>> is_envelope("not a dict")
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

"""
统一错误码定义

合并了 sdk/errors.py 和 typedefs/result.py 中的错误码定义。
提供 IntEnum 错误码（与 HTTP 状态码对齐）和字符串别名。

Usage:
    from plugin._types import ErrorCode, ERROR_NAMES
    
    # 使用错误码
    code = ErrorCode.VALIDATION_ERROR  # 400
    
    # 获取错误名称
    name = ERROR_NAMES[code]  # "VALIDATION_ERROR"
    
    # 在响应中使用
    return fail(ErrorCode.NOT_FOUND, "资源不存在")
"""
from __future__ import annotations

from enum import IntEnum
from typing import Dict


class ErrorCode(IntEnum):
    """统一错误码枚举
    
    与 HTTP 状态码对齐，便于 API 响应。
    
    分类：
    - 0: 成功
    - 4xx: 客户端错误
    - 5xx: 服务端错误
    - 1xxx: 插件特定错误
    
    Attributes:
        SUCCESS: 成功 (0)
        VALIDATION_ERROR: 参数验证失败 (400)
        UNAUTHORIZED: 未授权 (401)
        FORBIDDEN: 禁止访问 (403)
        NOT_FOUND: 资源不存在 (404)
        CONFLICT: 资源冲突 (409)
        RATE_LIMITED: 频率限制 (429)
        INTERNAL: 内部错误 (500)
        NOT_IMPLEMENTED: 未实现 (501)
        DEPENDENCY_MISSING: 依赖缺失 (502)
        NOT_READY: 服务未就绪 (503)
        TIMEOUT: 超时 (504)
        INVALID_RESPONSE: 响应格式无效 (422)
        PLUGIN_NOT_RUNNING: 插件未运行 (1001)
        PLUGIN_CRASHED: 插件崩溃 (1002)
        CIRCULAR_CALL: 循环调用 (1003)
        PLUGIN_RATE_LIMITED: 插件频率限制 (1004)
    """
    # 成功
    SUCCESS = 0
    
    # 客户端错误 (4xx)
    VALIDATION_ERROR = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    INVALID_RESPONSE = 422
    RATE_LIMITED = 429

    # 服务端错误 (5xx)
    INTERNAL = 500
    NOT_IMPLEMENTED = 501
    DEPENDENCY_MISSING = 502
    NOT_READY = 503
    TIMEOUT = 504
    
    # 插件特定错误 (1xxx)
    PLUGIN_NOT_RUNNING = 1001
    PLUGIN_CRASHED = 1002
    CIRCULAR_CALL = 1003
    PLUGIN_RATE_LIMITED = 1004
    
    # 向后兼容别名（映射到新值）
    @classmethod
    def from_string(cls, name: str) -> "ErrorCode":
        """从字符串名称获取错误码
        
        Args:
            name: 错误码名称（如 "VALIDATION_ERROR"）
        
        Returns:
            对应的 ErrorCode
        
        Raises:
            ValueError: 如果名称无效
        """
        name_upper = name.upper()
        # 处理向后兼容别名
        if name_upper == "EXECUTION_ERROR":
            return cls.INTERNAL
        if name_upper == "COMMUNICATION_ERROR":
            return cls.DEPENDENCY_MISSING
        if name_upper == "SERVICE_UNAVAILABLE":
            return cls.NOT_READY
        if name_upper == "INVALID_PARAMS":
            return cls.VALIDATION_ERROR
        
        try:
            return cls[name_upper]
        except KeyError as err:
            raise ValueError(f"Unknown error code: {name}") from err


# 错误码名称映射（用于序列化）
ERROR_NAMES: Dict[ErrorCode, str] = {member: member.name for member in ErrorCode}


def get_error_name(code: ErrorCode) -> str:
    """获取错误码的字符串名称"""
    return ERROR_NAMES.get(code, code.name)


def get_http_status(code: ErrorCode) -> int:
    """获取错误码对应的 HTTP 状态码
    
    对于插件特定错误 (1xxx)，返回 500。
    """
    if code.value >= 1000:
        return 500
    return code.value if code.value > 0 else 200


__all__ = [
    "ErrorCode",
    "ERROR_NAMES",
    "get_error_name",
    "get_http_status",
]

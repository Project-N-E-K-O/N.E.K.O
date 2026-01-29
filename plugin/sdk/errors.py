from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """标准错误码枚举
    
    定义了插件开发中常用的标准错误码。可以在fail()函数中使用这些错误码,
    也可以使用自定义字符串作为错误码。
    
    Attributes:
        VALIDATION_ERROR: 参数验证失败,输入数据不符合要求
        DEPENDENCY_MISSING: 缺少必需的依赖项(如其他插件、系统服务等)
        NOT_READY: 插件或服务尚未就绪,无法处理请求
        RATE_LIMITED: 请求频率超过限制,需要降低请求速率
        TIMEOUT: 操作超时,未能在规定时间内完成
        NOT_FOUND: 请求的资源不存在
        INTERNAL: 内部错误,插件内部发生了未预期的错误
        INVALID_RESPONSE: 响应格式无效或不符合预期
    
    Example:
        >>> from plugin.sdk import ErrorCode, fail
        >>> fail(ErrorCode.VALIDATION_ERROR, "参数name不能为空")
        >>> fail(ErrorCode.TIMEOUT, "数据库查询超时", retriable=True)
        >>> fail(ErrorCode.DEPENDENCY_MISSING, "需要插件 'data_processor'", 
        ...      details={"required_plugin": "data_processor"})
    """
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    NOT_READY = "NOT_READY"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL = "INTERNAL"
    INVALID_RESPONSE = "INVALID_RESPONSE"

"""
插件系统统一类型定义（已弃用）

此模块已弃用，请使用 plugin.types 代替。

Usage:
    # 旧方式（已弃用）
    from plugin.typedefs import Ok, Err, Result
    
    # 新方式（推荐）
    from plugin._types import Ok, Err, Result
"""
import warnings

warnings.warn(
    "plugin.typedefs is deprecated, use plugin.types instead",
    DeprecationWarning,
    stacklevel=2
)

# 从新位置重导出所有内容
from plugin._types import (
    # 错误码
    ErrorCode,
    ERROR_NAMES,
    get_error_name,
    get_http_status,
    # Result 类型
    Ok,
    Err,
    Result,
    ResultError,
    safe,
    async_safe,
    try_call,
    try_call_async,
    from_optional,
    collect_results,
    # 异常
    PluginError,
    PluginNotFoundError,
    PluginNotRunningError,
    PluginTimeoutError,
    PluginExecutionError,
    PluginCommunicationError,
    PluginLoadError,
    PluginImportError,
    PluginLifecycleError,
    PluginTimerError,
    PluginEntryNotFoundError,
    PluginMetadataError,
    PluginQueueError,
    # Protocol
    PluginContextProtocol,
)

__all__ = [
    # 错误码
    "ErrorCode",
    "ERROR_NAMES",
    "get_error_name",
    "get_http_status",
    # Result 类型
    "Ok",
    "Err",
    "Result",
    "ResultError",
    "safe",
    "async_safe",
    "try_call",
    "try_call_async",
    "from_optional",
    "collect_results",
    # 异常
    "PluginError",
    "PluginNotFoundError",
    "PluginNotRunningError",
    "PluginTimeoutError",
    "PluginExecutionError",
    "PluginCommunicationError",
    "PluginLoadError",
    "PluginImportError",
    "PluginLifecycleError",
    "PluginTimerError",
    "PluginEntryNotFoundError",
    "PluginMetadataError",
    "PluginQueueError",
    # Protocol
    "PluginContextProtocol",
]

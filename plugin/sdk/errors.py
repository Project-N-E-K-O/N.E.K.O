"""
错误码模块（向后兼容）

此模块已迁移到 plugin.types.errors，这里仅保留重导出。

Usage:
    # 推荐使用新位置
    from plugin._types import ErrorCode, ERROR_NAMES
    
    # 向后兼容（仍然可用）
    from plugin.sdk.errors import ErrorCode
"""
from plugin._types.errors import ErrorCode, ERROR_NAMES, get_error_name, get_http_status

__all__ = ["ErrorCode", "ERROR_NAMES", "get_error_name", "get_http_status"]

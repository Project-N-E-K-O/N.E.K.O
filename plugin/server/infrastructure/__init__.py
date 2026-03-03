"""
基础设施模块

提供共享的基础设施组件:线程池、工具函数、异常处理、认证等。
"""
from plugin.server.infrastructure.executor import _api_executor
from plugin.server.infrastructure.auth import require_admin, get_admin_code
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.infrastructure.error_handler import handle_plugin_error, safe_execute
from plugin.server.infrastructure.utils import now_iso

__all__ = [
    '_api_executor',
    'require_admin',
    'get_admin_code',
    'register_exception_handlers',
    'handle_plugin_error',
    'safe_execute',
    'now_iso',
]

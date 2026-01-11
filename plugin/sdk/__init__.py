"""
Plugin SDK 模块

提供插件开发工具包, 包括基类、事件系统和装饰器。
"""

from .version import SDK_VERSION
from .errors import ErrorCode
from .responses import ok, fail
from .decorators import (
    neko_plugin,
    on_event,
    plugin_entry,
    lifecycle,
    message,
    timer_interval,
    custom_event,  # 新增：自定义事件装饰器
)
from .base import NekoPluginBase, PluginMeta
from .config import PluginConfig
from .plugins import Plugins
from .events import EventMeta, EventHandler
from .system_info import SystemInfo
from .memory import MemoryClient

__all__ = [
    "SDK_VERSION",
    "ErrorCode",
    "ok",
    "fail",
    # 装饰器
    "neko_plugin",
    "on_event",
    "plugin_entry",
    "lifecycle",
    "message",
    "timer_interval",
    "custom_event",  # 新增
    # 基类和元数据
    "NekoPluginBase",
    "PluginMeta",
    "PluginConfig",
    "Plugins",
    "EventMeta",
    "EventHandler",
    "SystemInfo",
    "MemoryClient",
]

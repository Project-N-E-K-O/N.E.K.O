"""
Plugin Core 模块

提供核心运行时状态、上下文、进程管理和插件注册。
合并了原 runtime/ 模块的功能。
"""

from plugin.core.state import GlobalState, state
from plugin.core.context import PluginContext
from plugin.core.status import status_manager, PluginStatusManager
from plugin.core.registry import (
    load_plugins_from_toml,
    get_plugins,
    register_plugin,
    scan_static_metadata,
)
from plugin.core.host import PluginHost, PluginProcessHost
from plugin.core.communication import PluginCommunicationResourceManager

__all__ = [
    # 状态管理
    "state",
    "GlobalState",
    "PluginContext",
    # 状态管理器
    "status_manager",
    "PluginStatusManager",
    # 注册表
    "load_plugins_from_toml",
    "get_plugins",
    "register_plugin",
    "scan_static_metadata",
    # 进程管理
    "PluginHost",
    "PluginProcessHost",
    "PluginCommunicationResourceManager",
]

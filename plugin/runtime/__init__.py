"""
Plugin Runtime 模块（已弃用）

此模块已合并到 plugin.core，这里仅保留重导出以保持向后兼容。

Usage:
    # 旧方式（已弃用）
    from plugin.runtime import PluginProcessHost
    
    # 新方式（推荐）
    from plugin.core import PluginProcessHost
"""
import warnings

warnings.warn(
    "plugin.runtime is deprecated, use plugin.core instead",
    DeprecationWarning,
    stacklevel=2
)

# 从新位置重导出
from plugin.core.status import status_manager, PluginStatusManager
from plugin.core.registry import (
    load_plugins_from_toml,
    get_plugins,
    register_plugin,
    scan_static_metadata,
)
from plugin.core.host import PluginProcessHost
from plugin.core.communication import PluginCommunicationResourceManager

__all__ = [
    "status_manager",
    "PluginStatusManager",
    "load_plugins_from_toml",
    "get_plugins",
    "register_plugin",
    "scan_static_metadata",
    "PluginProcessHost",
    "PluginCommunicationResourceManager",
]


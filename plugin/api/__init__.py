"""
Plugin API 模块（已弃用）

此模块已弃用，请使用 plugin._types 代替。

Usage:
    # 旧方式（已弃用）
    from plugin.api import PluginMeta
    
    # 新方式（推荐）
    from plugin._types import PluginMeta
"""
import warnings

warnings.warn(
    "plugin.api is deprecated, use plugin._types instead",
    DeprecationWarning,
    stacklevel=2
)

# 从新位置重导出
from plugin._types import (
    PluginMeta,
    PluginType,
    PluginAuthor,
    PluginDependency,
)

__all__ = [
    "PluginMeta",
    "PluginType",
    "PluginAuthor",
    "PluginDependency",
]

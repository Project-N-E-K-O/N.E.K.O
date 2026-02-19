"""
Plugin API 模块

提供 API 数据模型和异常定义。
"""

from plugin.api.models import (
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

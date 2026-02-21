"""
SDK version declaration for plugin compatibility checks.

此模块从 plugin.types.version 重导出，保持向后兼容。
"""
from plugin._types.version import SDK_VERSION

__all__ = ["SDK_VERSION"]


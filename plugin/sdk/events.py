"""
插件事件系统模块（重导出）

此模块从 plugin.types.events 重导出，保持向后兼容。
推荐直接使用 plugin.types.events。
"""
from plugin._types.events import (
    EVENT_META_ATTR,
    StandardEventType,
    EventType,
    EventMeta,
    EventHandler,
)


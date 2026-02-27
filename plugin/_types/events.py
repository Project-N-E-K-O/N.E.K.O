"""
插件事件系统模块

提供事件元数据和事件处理器定义。
"""
from dataclasses import dataclass
from typing import Dict, Any, Callable, Literal, get_args

# 事件元数据属性名（用于标记事件处理器）
EVENT_META_ATTR = "__neko_event_meta__"

# 标准事件类型
StandardEventType = Literal[
    "plugin_entry",  # 对外可调用入口(plugin_entry) 目前已经实现
    "lifecycle",     # 生命周期相关事件（on_startup / on_shutdown）
    "message",       # 将来的消息事件（比如 on_message）
    "timer",         # 将来的定时事件
]

# 标准事件类型集合（自动与 StandardEventType 保持同步）
STANDARD_EVENT_TYPES: tuple[str, ...] = get_args(StandardEventType)

# 事件类型：标准类型或自定义字符串
EventType = str

@dataclass
class EventMeta:
    """事件元数据"""
    event_type: EventType  # 支持标准类型或自定义字符串
    id: str                     # 事件在"本插件内部"的 id，比如 "open" / "close" / "startup"
    name: str                   # 展示名
    description: str = ""
    input_schema: Dict[str, Any] | None = None

    # 以下字段主要给 plugin_entry / lifecycle 用
    kind: Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer"] = "action"
    auto_start: bool = False    # event_type == "lifecycle" 或 "plugin_entry" 时可用
    # 动态 entry 支持
    enabled: bool = True        # 是否启用，默认启用
    dynamic: bool = False       # 是否是动态创建的 entry
    # 预留更多字段（后续扩展用）
    metadata: Dict[str, Any] | None = None
    
    # 向后兼容别名（已弃用，将在 v2.0 移除）
    @property
    def extra(self) -> Dict[str, Any] | None:
        """已弃用，请使用 metadata"""
        import warnings
        warnings.warn("EventMeta.extra is deprecated, use metadata instead", DeprecationWarning, stacklevel=2)
        return self.metadata
    
    def is_custom_event(self) -> bool:
        """判断是否是自定义事件类型"""
        return self.event_type not in STANDARD_EVENT_TYPES


@dataclass
class EventHandler:
    """事件处理器"""
    meta: EventMeta
    handler: Callable  # 具体要调用的函数/方法


__all__ = [
    "EVENT_META_ATTR",
    "StandardEventType",
    "EventType",
    "EventMeta",
    "EventHandler",
]

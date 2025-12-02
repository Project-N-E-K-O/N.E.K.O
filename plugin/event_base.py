from dataclasses import dataclass
from typing import Dict, Any, Callable, Literal, Optional

EVENT_META_ATTR = "__neko_event_meta__"

EventType = Literal[
    "plugin_entry",  # 对外可调用入口(plugin_entry) 目前已经实现
    "lifecycle",     # 生命周期相关事件（on_startup / on_shutdown）
    "message",       # 将来的消息事件（比如 on_message）
    "timer",         # 将来的定时事件
]

@dataclass
class EventMeta:
    event_type: EventType
    id: str                     # 事件在“本插件内部”的 id，比如 "open" / "close" / "startup"
    name: str                   # 展示名
    description: str = ""
    input_schema: Dict[str, Any] | None = None

    # 以下字段主要给 plugin_entry / lifecycle 用
    kind: Literal["service", "action", "hook"] = "action"
    auto_start: bool = False    # event_type == "lifecycle" 或 "plugin_entry" 时可用
    # 预留更多字段（后续扩展用）
    extra: Dict[str, Any] | None = None


@dataclass
class EventHandler:
    meta: EventMeta
    handler: Callable  # 具体要调用的函数/方法

# neko_plugin_core/decorators.py
from typing import Type, Callable
from .plugin_base import PluginMeta, NEKO_PLUGIN_TAG
from .event_base import EventMeta, EVENT_META_ATTR
def neko_plugin(cls):
    """
    简单版插件装饰器：
    - 不接收任何参数
    - 只给类打一个标记，方便将来校验 / 反射
    元数据(id/name/description/version 等)全部从 plugin.toml 读取。
    """
    setattr(cls, NEKO_PLUGIN_TAG, True)
    return cls

def on_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: str = "action",
    auto_start: bool = False,
    extra: dict | None = None,
) -> Callable:
    """
    通用事件装饰器。
    - event_type: "plugin_entry" / "lifecycle" / "message" / "timer" ...
    - id: 在“本插件内部”的事件 id（不带插件 id）
    """
    def decorator(fn: Callable):
        meta = EventMeta(
            event_type=event_type,         # type: ignore[arg-type]
            id=id,
            name=name or id,
            description=description,
            input_schema=input_schema or {},
            kind=kind,                    # 对 plugin_entry: "service" / "action"
            auto_start=auto_start,
            extra=extra or {},
        )
        setattr(fn, EVENT_META_ATTR, meta)
        return fn
    return decorator


def plugin_entry(
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: str = "action",
    auto_start: bool = False,
    extra: dict | None = None,
) -> Callable:
    """
    语法糖：专门用来声明“对外可调用入口”的装饰器。
    本质上是 on_event(event_type="plugin_entry").
    """
    return on_event(
        event_type="plugin_entry",
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        extra=extra,
    )
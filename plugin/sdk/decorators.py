"""
插件装饰器模块

提供插件开发所需的装饰器。
"""
from typing import Type, Callable, Literal
from .base import PluginMeta, NEKO_PLUGIN_TAG
from .events import EventMeta, EVENT_META_ATTR


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
    - id: 在"本插件内部"的事件 id（不带插件 id）
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
    语法糖：专门用来声明"对外可调用入口"的装饰器。
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


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload"],
    name: str | None = None,
    description: str = "",
    extra: dict | None = None,
) -> Callable:
    """生命周期事件装饰器"""
    return on_event(
        event_type="lifecycle",
        id=id,
        name=name or id,
        description=description,
        input_schema={},   # 一般不需要参数
        kind="lifecycle",
        auto_start=False,
        extra=extra or {},
    )


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    source: str | None = None,
    extra: dict | None = None,
) -> Callable:
    """
    消息事件：比如处理聊天消息、总线事件等。
    """
    ex = dict(extra) if extra else {}
    if source:
        ex.setdefault("source", source)

    return on_event(
        event_type="message",
        id=id,
        name=name or id,
        description=description,
        input_schema=input_schema or {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "sender": {"type": "string"},
                "ts": {"type": "string"},
            },
        },
        kind="consumer",
        auto_start=True,   # runtime 可以根据这个自动订阅
        extra=ex,
    )


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    extra: dict | None = None,
) -> Callable:
    """
    固定间隔定时任务：每 N 秒执行一次。
    """
    ex = {"mode": "interval", "seconds": seconds}
    if extra:
        ex.update(extra)

    return on_event(
        event_type="timer",
        id=id,
        name=name or id,
        description=description or f"Run every {seconds}s",
        input_schema={},
        kind="timer",
        auto_start=auto_start,
        extra=ex,
    )


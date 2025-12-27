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


def custom_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: str = "custom",
    auto_start: bool = False,
    trigger_method: str = "message",  # "message" | "command" | "auto"
    extra: dict | None = None,
) -> Callable:
    """
    自定义事件装饰器：允许插件定义全新的事件类型。
    
    Args:
        event_type: 自定义事件类型名称（例如 "file_change", "user_action" 等）
        id: 事件ID（在插件内部唯一）
        name: 显示名称
        description: 事件描述
        input_schema: 输入参数schema（JSON Schema格式）
        kind: 事件种类，默认为 "custom"
        auto_start: 是否自动启动（如果为True，会在插件启动时自动执行）
        trigger_method: 触发方式
            - "message": 通过消息队列触发（推荐，异步）
            - "command": 通过命令队列触发（同步，类似 plugin_entry）
            - "auto": 自动启动（类似 timer auto_start）
        extra: 额外配置信息
    
    Returns:
        装饰器函数
    
    Example:
        @custom_event(
            event_type="file_change",
            id="on_file_modified",
            name="文件修改事件",
            description="当文件被修改时触发",
            trigger_method="message"
        )
        def handle_file_change(self, file_path: str, action: str):
            self.logger.info(f"File {file_path} was {action}")
    """
    # 验证 event_type 不是标准类型
    standard_types = ("plugin_entry", "lifecycle", "message", "timer")
    if event_type in standard_types:
        raise ValueError(
            f"Event type '{event_type}' is a standard type. "
            f"Use the corresponding decorator (@plugin_entry, @lifecycle, etc.) instead."
        )
    
    ex = dict(extra) if extra else {}
    ex["trigger_method"] = trigger_method
    
    return on_event(
        event_type=event_type,
        id=id,
        name=name or id,
        description=description,
        input_schema=input_schema or {},
        kind=kind,
        auto_start=auto_start,
        extra=ex,
    )


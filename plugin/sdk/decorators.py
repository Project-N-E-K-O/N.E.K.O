"""
插件装饰器模块

提供插件开发所需的装饰器。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Type, Callable, Literal, Union, overload, Any, Coroutine, Dict
from .base import PluginMeta, NEKO_PLUGIN_TAG
from .events import EventMeta, EVENT_META_ATTR

# Worker 装饰器的属性名
WORKER_MODE_ATTR = "_neko_worker_mode"


@dataclass
class WorkerConfig:
    """Worker 配置"""
    timeout: float = 30.0
    priority: int = 0


# 状态持久化配置的属性名
PERSIST_ATTR = "_neko_persist"
# 向后兼容别名
CHECKPOINT_ATTR = PERSIST_ATTR


def neko_plugin(cls):
    """
    简单版插件装饰器：
    - 不接收任何参数
    - 只给类打一个标记，方便将来校验 / 反射
    元数据(id/name/description/version 等)全部从 plugin.toml 读取。
    """
    setattr(cls, NEKO_PLUGIN_TAG, True)
    return cls


# Entry kind 类型（包含所有可能的 kind 值）
EntryKind = Literal["service", "action", "hook", "custom", "lifecycle", "consumer", "timer"]


def on_event(
    *,
    event_type: str,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,  # 向后兼容别名
    extra: dict | None = None,
) -> Callable:
    """
    通用事件装饰器。
    - event_type: "plugin_entry" / "lifecycle" / "message" / "timer" ...
    - id: 在"本插件内部"的事件 id（不带插件 id）
    - persist: 执行后是否保存状态（None=遵循 __persist_mode__）
    - checkpoint: persist 的向后兼容别名
    """
    # 向后兼容：checkpoint 参数映射到 persist
    effective_persist = persist if persist is not None else checkpoint
    
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
        # 设置 persist 配置（None 表示遵循类级别 __persist_mode__）
        if effective_persist is not None:
            setattr(fn, PERSIST_ATTR, effective_persist)
        return fn
    return decorator


def worker(timeout: float = 30.0, priority: int = 0):
    """
    Worker 模式装饰器
    
    标记函数应该在 worker 线程池中执行，而不是在命令循环线程中执行。
    可以叠加在 @plugin_entry、@lifecycle、@bus_subscribe 等装饰器上。
    
    Args:
        timeout: 超时时间（秒），默认 30.0
        priority: 优先级（数字越大优先级越高），默认 0
    
    Example:
        @worker(timeout=30.0)
        @plugin_entry(id="sync_task")
        def sync_task(self, param: str):
            # 同步代码，会在 worker 线程池中执行
            return ok(data={"result": param})
    """
    def decorator(func: Callable) -> Callable:
        # 附加 worker 配置到函数
        config = WorkerConfig(timeout=timeout, priority=priority)
        setattr(func, WORKER_MODE_ATTR, config)
        # 返回原函数（不包装）
        return func
    return decorator


class PluginDecorators:
    """插件装饰器命名空间，支持 @plugin.worker 等语法"""
    
    @staticmethod
    def worker(timeout: float = 30.0, priority: int = 0):
        """Worker 模式装饰器"""
        return worker(timeout=timeout, priority=priority)
    
    @staticmethod
    def entry(**kwargs):
        """Plugin entry 装饰器（别名）"""
        return plugin_entry(**kwargs)


# 创建全局实例
plugin = PluginDecorators()


def plugin_entry(
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,  # 向后兼容别名
    extra: dict | None = None,
) -> Callable:
    """
    语法糖：专门用来声明"对外可调用入口"的装饰器。
    本质上是 on_event(event_type="plugin_entry").
    
    Args:
        persist: 执行后是否保存状态
            - None: 遵循类级别 __persist_mode__ 配置
            - True: 强制启用状态保存
            - False: 强制禁用状态保存
        checkpoint: persist 的向后兼容别名
    """
    return on_event(
        event_type="plugin_entry",
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        kind=kind,
        auto_start=auto_start,
        persist=persist,
        checkpoint=checkpoint,
        extra=extra,
    )


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze"],
    name: str | None = None,
    description: str = "",
    extra: dict | None = None,
) -> Callable:
    """生命周期事件装饰器
    
    支持的生命周期事件：
    - startup: 插件启动时调用
    - shutdown: 插件停止时调用
    - reload: 插件重载时调用
    - freeze: 插件冻结前调用（可用于清理资源、保存额外状态）
    - unfreeze: 插件从冻结状态恢复后调用（可用于重新初始化资源）
    """
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
    kind: EntryKind = "custom",
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


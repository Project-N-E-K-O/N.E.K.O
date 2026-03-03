"""
插件装饰器模块

提供插件开发所需的装饰器。
"""
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Type, Callable, Literal, Union, overload, Any, Coroutine, Dict, List, Optional, Protocol, TypeVar, cast, runtime_checkable, get_type_hints
from .base import PluginMeta, NEKO_PLUGIN_TAG
from .events import EventMeta, EVENT_META_ATTR
from .hooks import HookMeta, HookHandler, HookTiming, HOOK_META_ATTR

# Worker 装饰器的属性名
WORKER_MODE_ATTR = "_neko_worker_mode"


@dataclass
class WorkerConfig:
    """Worker 配置"""
    timeout: float = 30.0
    priority: int = 0


# 状态持久化配置的属性名
PERSIST_ATTR = "_neko_persist"

# 向后兼容别名（已弃用，将在 v2.0 移除）
def _get_checkpoint_attr():
    import warnings
    warnings.warn(
        "CHECKPOINT_ATTR is deprecated, use PERSIST_ATTR instead. "
        "Will be removed in v2.0.",
        DeprecationWarning,
        stacklevel=2
    )
    return PERSIST_ATTR

# 保持向后兼容，但访问时会触发警告
CHECKPOINT_ATTR = PERSIST_ATTR  # 直接赋值，避免每次访问都警告


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

# Parameters to skip when auto-inferring schema from function signature.
_SKIP_PARAMS = frozenset({"self", "cls", "kwargs", "_ctx", "args"})

_PY_TYPE_TO_JSON: Dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _infer_schema_from_func(fn: Callable) -> Dict[str, Any]:
    """Auto-generate a JSON Schema object from a function's signature + type hints.

    Rules:
    - ``self``, ``cls``, ``**kwargs``, ``_ctx`` are skipped.
    - ``VAR_KEYWORD`` (``**kwargs``) params are skipped.
    - Parameters without a default value are added to ``required``.
    - Type hints are mapped to JSON Schema types via ``_PY_TYPE_TO_JSON``.
    - ``Optional[X]`` is mapped to ``{"type": ["<x>", "null"]}``.
    - ``Annotated[X, <desc_str>]`` extracts the string as ``description``.
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:
        hints = {}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for name, param in sig.parameters.items():
        if name in _SKIP_PARAMS:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue

        prop: Dict[str, Any] = {}
        hint = hints.get(name, param.annotation)

        # Unwrap Annotated[X, "desc", ...]
        origin = getattr(hint, "__class__", None)
        type_args = getattr(hint, "__args__", None)
        metadata_items = getattr(hint, "__metadata__", None)
        if metadata_items is not None and type_args:
            # typing.Annotated
            hint = type_args[0]
            for m in metadata_items:
                if isinstance(m, str):
                    prop["description"] = m
                    break

        # Unwrap Optional[X] → Union[X, None]
        type_origin = getattr(hint, "__origin__", None)
        if type_origin is Union:
            inner_args = [a for a in (getattr(hint, "__args__", ()) or ()) if a is not type(None)]
            if len(inner_args) == 1:
                # Optional[X]
                actual = inner_args[0]
                json_t = _PY_TYPE_TO_JSON.get(actual)
                if json_t:
                    prop["type"] = [json_t, "null"]
            # else: complex Union, skip type
        elif hint in _PY_TYPE_TO_JSON:
            prop["type"] = _PY_TYPE_TO_JSON[hint]
        elif hint is not inspect.Parameter.empty:
            # Unknown type — try str representation
            pass

        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def on_event(
    *,
    event_type: str,
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,  # 向后兼容别名
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
) -> Callable:
    """
    通用事件装饰器。
    - event_type: "plugin_entry" / "lifecycle" / "message" / "timer" ...
    - id: 在"本插件内部"的事件 id（不带插件 id）。
           None 时从被装饰函数名自动推断。
    - input_schema: JSON Schema dict。
           None 时从被装饰函数的签名 + type hints 自动推断。
    - persist: 执行后是否保存状态（None=遵循 __persist_mode__）
    - checkpoint: persist 的向后兼容别名
    - metadata: 额外的元数据字典
    - extra: metadata 的向后兼容别名（已弃用）
    """
    # 向后兼容：checkpoint 参数映射到 persist
    effective_persist = persist if persist is not None else checkpoint
    # 向后兼容：extra 参数映射到 metadata
    effective_metadata = metadata if metadata is not None else extra
    
    def decorator(fn: Callable):
        effective_id = id if id is not None else fn.__name__
        effective_schema = input_schema if input_schema is not None else _infer_schema_from_func(fn)
        meta = EventMeta(
            event_type=event_type,         # type: ignore[arg-type]
            id=effective_id,
            name=name or effective_id,
            description=description,
            input_schema=effective_schema,
            kind=kind,                    # 对 plugin_entry: "service" / "action"
            auto_start=auto_start,
            metadata=effective_metadata or {},
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


_PARAMS_MODEL_ATTR = "_neko_params_model"


def plugin_entry(
    id: str | None = None,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    params: type | None = None,
    kind: EntryKind = "action",
    auto_start: bool = False,
    persist: bool | None = None,
    checkpoint: bool | None = None,  # 向后兼容别名
    timeout: float | None = None,  # 自定义超时时间（秒），None 表示使用默认值
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
) -> Callable:
    """
    语法糖：专门用来声明"对外可调用入口"的装饰器。
    本质上是 on_event(event_type="plugin_entry").
    
    Args:
        id: Entry ID. None → auto-inferred from function name.
        name: Display name. None → same as id.
        input_schema: Explicit JSON Schema dict. None → auto-inferred from
            function signature (or ``params`` model if provided).
        params: Optional Pydantic BaseModel subclass. When provided:
            - ``input_schema`` is auto-extracted via ``model.model_json_schema()``.
            - The model class is attached to the function for optional runtime
              validation (accessible via ``_PARAMS_MODEL_ATTR``).
        persist: 执行后是否保存状态
            - None: 遵循类级别 __persist_mode__ 配置
            - True: 强制启用状态保存
            - False: 强制禁用状态保存
        checkpoint: persist 的向后兼容别名
        timeout: 自定义超时时间（秒）
            - None: 使用默认超时（PLUGIN_TRIGGER_TIMEOUT，默认 10 秒）
            - 0 或负数: 禁用超时检测（无限等待）
            - 正数: 使用指定的超时时间
        metadata: 额外的元数据字典
        extra: metadata 的向后兼容别名（已弃用）
    """
    # Pydantic model → extract JSON Schema
    effective_schema = input_schema
    if params is not None and effective_schema is None:
        _model_json_schema = getattr(params, "model_json_schema", None)
        if callable(_model_json_schema):
            effective_schema = cast(Dict[str, Any], _model_json_schema())
        else:
            raise TypeError(f"params must be a Pydantic BaseModel subclass, got {params!r}")

    # 向后兼容：extra 参数映射到 metadata
    effective_metadata = dict(metadata) if metadata else (dict(extra) if extra else {})
    if timeout is not None:
        effective_metadata["timeout"] = timeout

    _inner = on_event(
        event_type="plugin_entry",
        id=id,
        name=name,
        description=description,
        input_schema=effective_schema,
        kind=kind,
        auto_start=auto_start,
        persist=persist,
        checkpoint=checkpoint,
        metadata=effective_metadata if effective_metadata else None,
    )

    if params is None:
        return _inner

    # Wrap the on_event decorator to also attach the params model.
    def decorator(fn: Callable) -> Callable:
        fn = _inner(fn)
        setattr(fn, _PARAMS_MODEL_ATTR, params)
        return fn
    return decorator


def lifecycle(
    *,
    id: Literal["startup", "shutdown", "reload", "freeze", "unfreeze", "config_change"],
    name: str | None = None,
    description: str = "",
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
) -> Callable:
    """生命周期事件装饰器
    
    支持的生命周期事件：
    - startup: 插件启动时调用
    - shutdown: 插件停止时调用
    - reload: 插件重载时调用
    - freeze: 插件冻结前调用（可用于清理资源、保存额外状态）
    - unfreeze: 插件从冻结状态恢复后调用（可用于重新初始化资源）
    - config_change: 配置热更新时调用（接收 old_config, new_config, mode 参数）
    """
    effective_metadata = metadata if metadata is not None else extra
    return on_event(
        event_type="lifecycle",
        id=id,
        name=name or id,
        description=description,
        input_schema={},   # 一般不需要参数
        kind="lifecycle",
        auto_start=False,
        metadata=effective_metadata or {},
    )


def message(
    *,
    id: str,
    name: str | None = None,
    description: str = "",
    input_schema: dict | None = None,
    source: str | None = None,
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
) -> Callable:
    """
    消息事件：比如处理聊天消息、总线事件等。
    """
    effective_metadata = dict(metadata) if metadata else (dict(extra) if extra else {})
    if source:
        effective_metadata.setdefault("source", source)

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
        metadata=effective_metadata,
    )


def timer_interval(
    *,
    id: str,
    seconds: int,
    name: str | None = None,
    description: str = "",
    auto_start: bool = True,
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
) -> Callable:
    """
    固定间隔定时任务：每 N 秒执行一次。
    """
    effective_metadata = {"mode": "interval", "seconds": seconds}
    if metadata:
        effective_metadata.update(metadata)
    elif extra:
        effective_metadata.update(extra)

    return on_event(
        event_type="timer",
        id=id,
        name=name or id,
        description=description or f"Run every {seconds}s",
        input_schema={},
        kind="timer",
        auto_start=auto_start,
        metadata=effective_metadata,
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
    metadata: dict | None = None,
    extra: dict | None = None,  # 向后兼容别名，已弃用
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
        metadata: 额外配置信息
        extra: metadata 的向后兼容别名（已弃用）
    
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
    
    effective_metadata = dict(metadata) if metadata else (dict(extra) if extra else {})
    effective_metadata["trigger_method"] = trigger_method
    
    return on_event(
        event_type=event_type,
        id=id,
        name=name or id,
        description=description,
        input_schema=input_schema or {},
        kind=kind,
        auto_start=auto_start,
        metadata=effective_metadata,
    )


# ==================== Hook 函数签名类型定义 ====================
# 使用 Protocol 定义不同 timing 的 Hook 函数签名，让 IDE 提供更好的类型提示

# Hook 函数返回类型
BeforeHookResult = Optional[Dict[str, Any]]  # None=继续, dict=阻止或修改参数
AfterHookResult = Dict[str, Any]  # 返回修改后的结果
AroundHookResult = Dict[str, Any]  # 返回最终结果
ReplaceHookResult = Dict[str, Any]  # 返回替换后的结果

# Hook 函数类型（用于类型提示）
BeforeHookFn = Callable[..., Union[BeforeHookResult, Coroutine[Any, Any, BeforeHookResult]]]
AfterHookFn = Callable[..., Union[AfterHookResult, Coroutine[Any, Any, AfterHookResult]]]
AroundHookFn = Callable[..., Union[AroundHookResult, Coroutine[Any, Any, AroundHookResult]]]
ReplaceHookFn = Callable[..., Union[ReplaceHookResult, Coroutine[Any, Any, ReplaceHookResult]]]

_HookFn = TypeVar("_HookFn", bound=Callable[..., Any])


@overload
def hook(
    target: str,
    timing: Literal["before"],
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[BeforeHookFn], BeforeHookFn]:
    """before Hook: 在目标 entry 执行前执行
    
    Hook 函数签名: (self, entry_id: str, params: dict, **_) -> Optional[dict]
    - 返回 None: 继续执行原始 handler
    - 返回 dict (含 code/message/data): 阻止执行，直接返回该结果
    - 返回 dict (不含上述字段): 作为修改后的 params 继续执行
    """
    ...


@overload
def hook(
    target: str,
    timing: Literal["after"],
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[AfterHookFn], AfterHookFn]:
    """after Hook: 在目标 entry 执行后执行
    
    Hook 函数签名: (self, entry_id: str, params: dict, result: dict, **_) -> dict
    - 接收 result 参数（原始 handler 的返回值）
    - 返回修改后的结果
    """
    ...


@overload
def hook(
    target: str,
    timing: Literal["around"],
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[AroundHookFn], AroundHookFn]:
    """around Hook: 包裹目标 entry
    
    Hook 函数签名: (self, entry_id: str, params: dict, next_handler: Callable, **_) -> dict
    - 接收 next_handler 参数，可以控制是否执行原始 handler
    - 调用 await next_handler(params) 执行下一个 hook 或原始 handler
    - 返回最终结果
    """
    ...


@overload
def hook(
    target: str,
    timing: Literal["replace"],
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[ReplaceHookFn], ReplaceHookFn]:
    """replace Hook: 完全替换目标 entry
    
    Hook 函数签名: (self, entry_id: str, params: dict, original_handler: Callable, **_) -> dict
    - 接收 original_handler 参数，可以选择性调用原始 handler
    - 返回替换后的结果
    """
    ...


@overload
def hook(
    target: str,
    timing: HookTiming = "before",
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[_HookFn], _HookFn]:
    """Hook 装饰器（通用签名）"""
    ...


def hook(
    target: str,
    timing: HookTiming = "before",
    priority: int = 0,
    condition: Optional[str] = None,
) -> Callable[[_HookFn], _HookFn]:
    """Hook 装饰器
    
    用于声明一个方法为 Hook，可以在目标 entry 执行前/后/周围执行。
    支持插件内部中间件和跨插件 Hook 两种场景。
    
    Args:
        target: Hook 目标
            - 插件内: "entry_id" - Hook 当前插件/Router 的指定 entry
            - 插件内: "*" - Hook 当前插件/Router 的所有 entry
            - 跨插件: "plugin_id.entry_id" - Hook 其他插件的指定 entry
        timing: 执行时机
            - "before": 在目标 entry 执行前执行
                - 返回 None: 继续执行
                - 返回 dict: 阻止执行，直接返回该结果
                - 返回修改后的 params: 修改参数后继续执行
            - "after": 在目标 entry 执行后执行
                - 接收 result 参数，可以修改返回值
            - "around": 包裹目标 entry
                - 接收 next_handler 参数，可以控制是否执行原始 handler
            - "replace": 完全替换目标 entry
                - 接收 original_handler 参数，可以选择性调用
        priority: 优先级（越大越先执行），默认 0
        condition: 条件表达式或方法名（可选）
            - 字符串: 当前类的方法名，返回 True 才执行 Hook
    
    Returns:
        装饰器函数
    
    Example - 插件内中间件:
        >>> class MyRouter(PluginRouter):
        ...     @hook(target="*", timing="before")
        ...     async def log_all_calls(self, entry_id: str, params: dict, **_):
        ...         '''Hook 所有 entry，记录调用日志'''
        ...         self.logger.info(f"Calling {entry_id} with {params}")
        ...         return None  # 继续执行
        ...
        ...     @hook(target="save", timing="before", priority=10)
        ...     async def validate_save(self, params: dict, **_):
        ...         '''验证 save entry 的参数'''
        ...         if not params.get("data", {}).get("name"):
        ...             return fail(message="name is required")  # 阻止执行
        ...         return None
        ...
        ...     @hook(target="load", timing="after")
        ...     async def cache_result(self, entry_id: str, result: dict, **_):
        ...         '''缓存 load 结果'''
        ...         self._cache[entry_id] = result
        ...         return result  # 可以修改返回值
    
    Example - 跨插件 Hook (扩展插件):
        >>> class ExtensionRouter(PluginRouter):
        ...     @hook(target="core_plugin.save", timing="before")
        ...     async def extend_save(self, params: dict, **_):
        ...         '''Hook 其他插件的 save entry'''
        ...         params["extended"] = True
        ...         return params  # 修改参数后继续
        ...
        ...     @hook(target="core_plugin.save", timing="around")
        ...     async def wrap_save(self, params: dict, next_handler, **_):
        ...         '''包裹 save entry'''
        ...         self.logger.info("Before save")
        ...         result = await next_handler(params)
        ...         self.logger.info("After save")
        ...         return result
    
    Note:
        - Hook 方法的签名根据 timing 不同而不同:
            - before: (self, entry_id: str, params: dict, **_) -> Optional[dict]
            - after: (self, entry_id: str, params: dict, result: dict, **_) -> dict
            - around: (self, entry_id: str, params: dict, next_handler: Callable, **_) -> dict
            - replace: (self, entry_id: str, params: dict, original_handler: Callable, **_) -> dict
        - 跨插件 Hook 需要在 plugin.toml 中声明权限
    """
    def decorator(fn: _HookFn) -> _HookFn:
        meta = HookMeta(
            target=target,
            timing=timing,
            priority=priority,
            condition=condition,
        )
        setattr(fn, HOOK_META_ATTR, meta)
        return fn
    return decorator


# 便捷别名
before_entry = lambda target="*", priority=0, condition=None: hook(target, "before", priority, condition)
after_entry = lambda target="*", priority=0, condition=None: hook(target, "after", priority, condition)
around_entry = lambda target="*", priority=0, condition=None: hook(target, "around", priority, condition)
replace_entry = lambda target, priority=0, condition=None: hook(target, "replace", priority, condition)


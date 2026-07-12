from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import time
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Final,
    Literal,
    Iterator,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    TYPE_CHECKING,
    overload,
    Union,
    cast,
)

from plugin.core.bus.bus_list import (
    BusListCore,
    _apply_reload_inplace_basic,
)

if TYPE_CHECKING:
    from plugin.core.bus.events import EventList
    from plugin.core.bus.lifecycle import LifecycleList
    from plugin.core.bus.memory import MemoryList
    from plugin.core.bus.messages import MessageList
    from plugin.core.bus.conversations import ConversationList

from plugin.core.bus.rev import dispatch_bus_change


TRecord = TypeVar("TRecord", bound="BusRecord")
BusChangeOp = Literal["add", "del", "change"]
DedupeKey = Tuple[str, Any]


class BusChange:
    ADD: Final[BusChangeOp] = "add"
    DEL: Final[BusChangeOp] = "del"
    CHANGE: Final[BusChangeOp] = "change"


class _MessageClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        timeout: float = 5.0,
    ) -> "MessageList": ...


class _EventClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "EventList": ...


class _LifecycleClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "LifecycleList": ...


class _MemoryClientProto(Protocol):
    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> "MemoryList": ...


class _ConversationClientProto(Protocol):
    def get(
        self,
        *,
        conversation_id: Optional[str] = None,
        max_count: int = 50,
        since_ts: Optional[float] = None,
        timeout: float = 5.0,
    ) -> "ConversationList": ...
    
    def get_by_id(
        self,
        conversation_id: str,
        *,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "ConversationList": ...


class BusHubProtocol(Protocol):
    """Bus Hub 协议，提供对各种 Bus 客户端的访问
    
    Attributes:
        messages: 消息客户端，用于查询消息
        events: 事件客户端，用于查询事件
        lifecycle: 生命周期客户端，用于查询生命周期事件
        memory: 内存客户端，用于查询内存数据
        conversations: 对话客户端，用于查询对话上下文
    """
    @property
    def messages(self) -> _MessageClientProto: ...

    @property
    def events(self) -> _EventClientProto: ...

    @property
    def lifecycle(self) -> _LifecycleClientProto: ...

    @property
    def memory(self) -> _MemoryClientProto: ...

    @property
    def conversations(self) -> _ConversationClientProto: ...


class BusReplayContext(Protocol):
    bus: BusHubProtocol

    # Internal helper used by SDK when running inside plugin process.
    # Exposed here for typing completeness; actual implementation lives on PluginContext.
    def _send_request_and_wait(
        self,
        *,
        method_name: str,
        request_type: str,
        request_data: Dict[str, Any],
        timeout: float,
        wrap_result: bool = True,
        **kwargs: Any,
    ) -> Any: ...


def parse_iso_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


@dataclass(frozen=True)
class BusFilter:
    kind: Optional[str] = None
    type: Optional[str] = None
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    kind_re: Optional[str] = None
    type_re: Optional[str] = None
    plugin_id_re: Optional[str] = None
    source_re: Optional[str] = None
    content_re: Optional[str] = None
    priority_min: Optional[int] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None


@dataclass(frozen=True, slots=True)
class BusRecord:
    kind: str
    type: str
    timestamp: Optional[float]
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    priority: int = 0
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def dump(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata or {}),
            "raw": dict(self.raw or {}),
        }


class BusFilterError(ValueError):
    pass


class NonReplayableTraceError(RuntimeError):
    pass


@dataclass(frozen=True)
class BusOp:
    name: str
    params: Dict[str, Any]
    at: float


@dataclass(frozen=True)
class TraceNode:
    op: str
    params: Dict[str, Any]
    at: float

    def dump(self) -> Dict[str, Any]:
        return {
            "op": self.op,
            "params": dict(self.params) if isinstance(self.params, dict) else {},
            "at": self.at,
        }

    def explain(self) -> str:
        if self.params:
            return f"{self.op}({self.params})"
        return f"{self.op}()"


@dataclass(frozen=True)
class GetNode(TraceNode):
    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "get"
        return base


@dataclass(frozen=True)
class UnaryNode(TraceNode):
    child: TraceNode

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "unary"
        base["child"] = self.child.dump()
        return base

    def explain(self) -> str:
        return self.child.explain() + " -> " + super().explain()


class BusList(BusListCore, Generic[TRecord]):
    def __init__(
        self,
        items: Sequence[TRecord],
        *,
        ctx: Optional[BusReplayContext] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[TraceNode] = None,
    ):
        # Optimization: avoid unnecessary list() copy if items is already a list
        self._items: List[TRecord] = items if isinstance(items, list) else list(items)
        self._ctx: Optional[BusReplayContext] = ctx
        self._trace: Tuple[BusOp, ...] = tuple(trace or ())
        self._plan: Optional[TraceNode] = plan
        self._cache_valid: bool = True

    def _is_lazy_mode(self) -> bool:
        return self._ctx is not None and self._plan is not None

    def _invalidate_cache(self) -> None:
        if self._is_lazy_mode():
            self._cache_valid = False

    def _ensure_materialized(self) -> None:
        if not self._is_lazy_mode():
            return
        if self._cache_valid:
            return
        ctx = self._ctx
        plan = self._plan
        if ctx is None or plan is None:
            return
        refreshed = self._replay_plan(ctx, plan)
        self._items = list(refreshed.dump_records())
        if hasattr(self, "plugin_id") and hasattr(refreshed, "plugin_id"):
            with suppress(Exception):
                setattr(self, "plugin_id", getattr(refreshed, "plugin_id"))
        self._cache_valid = True

    def __iter__(self) -> Iterator[TRecord]:
        self._ensure_materialized()
        return iter(self._items)

    def __len__(self) -> int:
        self._ensure_materialized()
        return len(self._items)

    def count(self) -> int:
        self._ensure_materialized()
        return len(self._items)

    def size(self) -> int:
        self._ensure_materialized()
        return len(self._items)

    def __getitem__(self, idx: int) -> TRecord:
        self._ensure_materialized()
        return self._items[idx]

    def dump(self) -> List[Dict[str, Any]]:
        """Dump records as JSON-serializable dicts.

        中文: 将列表中的每条记录转换为 dict(通常来自 record.dump()), 便于序列化/日志输出.
        English: Convert each record to a dict (typically via record.dump()) for serialization/logging.
        """
        self._ensure_materialized()
        return [x.dump() for x in self._items]

    def dump_records(self) -> List[TRecord]:
        """Return a shallow copy of the underlying record list.

        中文: 返回当前记录列表的浅拷贝, 直接得到原始 record 对象.
        English: Return a shallow copy of record objects.
        """
        self._ensure_materialized()
        return list(self._items)

    @property
    def trace(self) -> Tuple[BusOp, ...]:
        return self._trace

    def trace_dump(self) -> List[Dict[str, Any]]:
        """Dump the recorded query trace.

        中文: 返回 trace 的可序列化版本, 用于调试/展示链式调用做了什么.
        English: Return a serializable trace describing the chained operations.

        Note:
            - trace 仅用于可观测性, 不保证可重放.
        """
        return [
            {
                "name": op.name,
                "params": dict(op.params) if isinstance(op.params, dict) else {},
                "at": op.at,
            }
            for op in self._trace
        ]

    def trace_tree_dump(self) -> Optional[Dict[str, Any]]:
        """Dump the replayable plan tree (if available).

        中文: 返回可重放的 plan(TraceNode) 树结构, 用于 watcher/reload.
        English: Return a replayable plan tree used by reload()/watch().

        Returns:
            - dict: plan tree
            - None: when no replayable plan is available.
        """
        if self._plan is None:
            return None
        return self._plan.dump()

    def explain(self) -> str:
        """Explain how this BusList is produced.

        中文: 生成当前列表的“查询表达式”字符串, 用于调试/打印.
        English: Return a human-readable query expression for debugging.

        Note:
            - 有 plan 时优先用 plan.explain() (更准确).
            - 无 plan 时退化为 trace 串联.
        """
        if self._plan is not None:
            return self._plan.explain()
        parts: List[str] = []
        for op in self._trace:
            if op.params:
                parts.append(f"{op.name}({op.params})")
            else:
                parts.append(f"{op.name}()")
        return " -> ".join(parts) if parts else "<no-trace>"

    def _add_trace(self, name: str, params: Optional[Dict[str, Any]] = None) -> Tuple[BusOp, ...]:
        p = params if isinstance(params, dict) else {}
        return self._trace + (BusOp(name=name, params=p, at=time.time()),)

    def _add_plan_unary(self, op: str, params: Optional[Dict[str, Any]] = None) -> Optional[TraceNode]:
        if self._plan is None:
            return None
        p = params if isinstance(params, dict) else {}
        return UnaryNode(op=op, params=p, at=time.time(), child=self._plan)

    def _construct(
        self,
        items: Sequence[TRecord],
        trace: Tuple[BusOp, ...],
        plan: Optional[TraceNode],
    ) -> "BusList[TRecord]":
        kwargs: Dict[str, Any] = {
            "ctx": getattr(self, "_ctx", None),
            "trace": trace,
            "plan": plan,
        }
        if hasattr(self, "plugin_id"):
            kwargs["plugin_id"] = getattr(self, "plugin_id")
        ctor = cast(Callable[..., "BusList[TRecord]"], self.__class__)
        try:
            out = ctor(items, **kwargs)
        except TypeError:
            kwargs.pop("plugin_id", None)
            out = ctor(items, **kwargs)
        with suppress(Exception):
            if getattr(self, "_ctx", None) is not None and plan is not None:
                out._cache_valid = False
        return out

    def sort(
        self,
        *,
        by: Optional[Union[str, Sequence[str]]] = None,
        key: Optional[Callable[[TRecord], Any]] = None,
        cast: Optional[str] = None,
        reverse: bool = False,
    ) -> "BusList[TRecord]":
        """Return a new list sorted by fields or a custom key.

        中文: 返回一个按字段或自定义 key 排序后的新 BusList.
        English: Return a new BusList sorted by fields or a custom key.

        Args:
            by:
                中文: 按哪些字段排序. 可以是单个字段名或字段名列表. 为空时会尝试默认字段
                (timestamp/created_at/time).
                English: Sort by field name(s). If omitted, tries default fields
                (timestamp/created_at/time).
            key:
                中文: 自定义排序函数. 与 by 互斥.
                English: Custom sort key callable. Mutually exclusive with by.
            cast:
                中文: 对字段值进行简单类型转换, 支持 "int"/"float"/"str".
                English: Optional value casting for field values: "int"/"float"/"str".
            reverse:
                中文: 是否倒序.
                English: Sort descending when True.

        Note:
            - sort(key=callable) 无法被 reload() 重放.
            - sort(by=...) 可以被 reload()/watch() 重放.
        """
        if key is not None and by is not None:
            raise ValueError("Specify only one of 'key' or 'by'")

        if key is not None and self._is_lazy_mode():
            raise NonReplayableTraceError("lazy list cannot use sort(key=callable); use sort(by=...) only")

        if key is None:
            if by is None:
                by_fields: List[str] = ["timestamp", "created_at", "time"]
            elif isinstance(by, str):
                by_fields = [by]
            else:
                by_fields = list(by)

            def key_func(x: TRecord) -> Tuple[Tuple[int, Any], ...]:
                return tuple(
                    self._sort_key(self._get_sort_field(x, f), cast)
                    for f in by_fields
                )

            sort_key: Callable[[TRecord], Any] = key_func
        else:
            sort_key = key

        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = sorted(self._items, key=sort_key, reverse=reverse)
        trace = self._add_trace(
            "sort",
            {
                "by": by,
                "key": getattr(key, "__name__", "<callable>") if key is not None else None,
                "cast": cast,
                "reverse": reverse,
            },
        )
        plan = self._add_plan_unary(
            "sort",
            {
                "by": by,
                "key": getattr(key, "__name__", "<callable>") if key is not None else None,
                "cast": cast,
                "reverse": reverse,
            },
        )
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def __eq__(self, other: object) -> bool:
        if other is self:
            return True
        if not isinstance(other, BusList):
            return False
        if type(self) is not type(other):
            return False
        if len(self._items) != len(other._items):
            return False
        return [self._dedupe_key(x) for x in self._items] == [other._dedupe_key(x) for x in other._items]

    def filter(
        self,
        flt: Optional[BusFilter] = None,
        *,
        strict: bool = True,
        **kwargs: Any,
    ) -> "BusList[TRecord]":
        """Filter records by structured conditions.

        中文: 根据 BusFilter/关键字参数过滤记录, 返回新的 BusList.
        English: Filter records using BusFilter or keyword arguments, returning a new BusList.

        Args:
            flt:
                中文: 预构建的 BusFilter, 为空则根据 kwargs 创建.
                English: Optional BusFilter. When None, build from kwargs.
            strict:
                中文: 严格模式. 正则非法/类型转换失败时抛异常.
                English: Strict mode. Raise on invalid regex / invalid numeric conversions.
            **kwargs:
                中文: BusFilter 字段快捷写法, 例如 source=..., type=..., priority_min=...
                English: Convenience fields for BusFilter (e.g. source/type/priority_min...).

        Returns:
            一个新的 BusList.

        Note:
            - filter(...) 是可重放的, 可用于 reload()/watch().
        """
        if flt is None:
            flt = BusFilter(**kwargs)

        # Pre-extract filter values to avoid repeated attribute access
        f_kind = flt.kind
        f_type = flt.type
        f_plugin_id = flt.plugin_id
        f_source = flt.source
        f_kind_re = flt.kind_re
        f_type_re = flt.type_re
        f_plugin_id_re = flt.plugin_id_re
        f_source_re = flt.source_re
        f_content_re = flt.content_re
        f_priority_min = flt.priority_min
        f_since_ts = flt.since_ts
        f_until_ts = flt.until_ts

        # Pre-compile regex patterns if needed
        re_kind = re.compile(f_kind_re) if f_kind_re else None
        re_type = re.compile(f_type_re) if f_type_re else None
        re_plugin_id = re.compile(f_plugin_id_re) if f_plugin_id_re else None
        re_source = re.compile(f_source_re) if f_source_re else None
        re_content = re.compile(f_content_re) if f_content_re else None

        # Pre-convert numeric filters
        pmin_i: Optional[int] = None
        if f_priority_min is not None:
            try:
                pmin_i = int(f_priority_min)
            except Exception:
                if strict:
                    raise BusFilterError(f"Invalid priority_min: {f_priority_min!r}")
        since_f: Optional[float] = None
        if f_since_ts is not None:
            try:
                since_f = float(f_since_ts)
            except Exception:
                if strict:
                    raise BusFilterError(f"Invalid since_ts: {f_since_ts!r}")
        until_f: Optional[float] = None
        if f_until_ts is not None:
            try:
                until_f = float(f_until_ts)
            except Exception:
                if strict:
                    raise BusFilterError(f"Invalid until_ts: {f_until_ts!r}")

        # Check if we have any regex filters
        has_regex = bool(re_kind or re_type or re_plugin_id or re_source or re_content)

        def _match(x: BusRecord) -> bool:
            # Fast equality checks first
            if f_kind is not None and x.kind != f_kind:
                return False
            if f_type is not None and x.type != f_type:
                return False
            if f_plugin_id is not None and x.plugin_id != f_plugin_id:
                return False
            if f_source is not None and x.source != f_source:
                return False

            # Numeric filters
            if pmin_i is not None and x.priority < pmin_i:
                return False
            if since_f is not None:
                ts = x.timestamp
                if ts is None or ts < since_f:
                    return False
            if until_f is not None:
                ts = x.timestamp
                if ts is None or ts > until_f:
                    return False

            # Regex filters (slower path)
            if has_regex:
                if re_kind is not None:
                    if x.kind is None or re_kind.search(x.kind) is None:
                        return False
                if re_type is not None:
                    if x.type is None or re_type.search(x.type) is None:
                        return False
                if re_plugin_id is not None:
                    if x.plugin_id is None or re_plugin_id.search(x.plugin_id) is None:
                        return False
                if re_source is not None:
                    if x.source is None or re_source.search(x.source) is None:
                        return False
                if re_content is not None:
                    if x.content is None or re_content.search(x.content) is None:
                        return False

            return True

        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = [item for item in self._items if _match(item)]
        params: Dict[str, Any] = {}
        try:
            params.update({k: v for k, v in vars(flt).items() if v is not None})
        except Exception:
            params["flt"] = str(flt)
        params["strict"] = strict
        trace = self._add_trace("filter", params)
        plan = self._add_plan_unary("filter", params)
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where(self, predicate: Callable[[TRecord], bool]) -> "BusList[TRecord]":
        """Filter using an arbitrary Python predicate.

        中文: 使用任意 Python 函数 predicate 过滤记录, 返回新 BusList.
        English: Filter with an arbitrary Python predicate callable.

        Warning:
            - where(predicate) 由于 predicate 不可序列化/不可重放, reload()/watch() 无法重放这一步.
            - 该操作不可序列化或重放；需要 reload()/watch() 时使用 filter(...).
        """
        if self._is_lazy_mode():
            raise NonReplayableTraceError("lazy list cannot use where(predicate); use filter(...) instead")
        items = [item for item in self._items if predicate(item)]
        trace = self._add_trace(
            "where",
            {"predicate": getattr(predicate, "__name__", "<callable>")},
        )
        # Not replayable: predicate is arbitrary callable.
        plan = self._add_plan_unary("where", {"predicate": getattr(predicate, "__name__", "<callable>")})
        return self._construct(items, trace, plan)

    def limit(self, n: int) -> "BusList[TRecord]":
        """Limit the number of records.

        中文: 截取前 n 条记录并返回新 BusList.
        English: Return a new BusList containing at most the first n records.

        Note:
            - n <= 0 时返回空列表.
            - limit(...) 是可重放的, 可用于 reload()/watch().
        """
        nn = int(n)
        if nn <= 0:
            trace = self._add_trace("limit", {"n": nn})
            plan = self._add_plan_unary("limit", {"n": nn})
            out = self._construct([], trace, plan)
            out._invalidate_cache()
            return out
        trace = self._add_trace("limit", {"n": nn})
        plan = self._add_plan_unary("limit", {"n": nn})
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = self._items[:nn]
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def _replay_plan(self, ctx: BusReplayContext, plan: TraceNode) -> "BusList[TRecord]":
        def _as_eager(lst: Any) -> Any:
            with suppress(Exception):
                lst._ctx = None
                lst._cache_valid = True
            return lst

        def _replay(node: TraceNode) -> "BusList[TRecord]":
            if isinstance(node, GetNode):
                bus = str(node.params.get("bus") or "").strip()
                params = dict(node.params.get("params") or {})
                if bus == "messages":
                    return _as_eager(ctx.bus.messages.get(**params))
                elif bus == "events":
                    return _as_eager(ctx.bus.events.get(**params))
                elif bus == "lifecycle":
                    return _as_eager(ctx.bus.lifecycle.get(**params))
                raise NonReplayableTraceError(f"Unknown bus for reload: {bus!r}")

            if isinstance(node, UnaryNode):
                base = _as_eager(_replay(node.child))
                if node.op == "filter":
                    p = dict(node.params)
                    strict = bool(p.pop("strict", True))
                    return base.filter(strict=strict, **p)
                if node.op == "limit":
                    return base.limit(int(node.params.get("n", 0)))
                if node.op == "sort":
                    if node.params.get("key") is not None:
                        raise NonReplayableTraceError("reload cannot replay sort(key=callable); use sort(by=...) only")
                    return base.sort(
                        by=node.params.get("by"),
                        cast=node.params.get("cast"),
                        reverse=bool(node.params.get("reverse", False)),
                    )
                if node.op == "where":
                    raise NonReplayableTraceError("reload cannot replay where(predicate); use filter(...) instead")
                raise NonReplayableTraceError(f"Unknown unary op for reload: {node.op!r}")

            raise NonReplayableTraceError(f"Unknown plan node type: {type(node).__name__}")
        return _replay(plan)

    @overload
    def reload(self, ctx: BusReplayContext) -> "BusList[TRecord]": ...

    @overload
    def reload(self, ctx: None = None) -> "BusList[TRecord]": ...

    def reload(
        self,
        ctx: Optional[BusReplayContext] = None,
    ) -> "BusList[TRecord]":
        """Replay the recorded plan against live bus data.

        中文: 使用可重放 plan 重新从 bus 拉取数据并应用同样的链式操作, 返回最新 BusList.
        English: Reload from bus by replaying the stored plan and operations.

        Requirements:
            - 必须是 replayable plan (通常由 get()/filter()/sort(by=...)/limit() 组合产生).
        """
        if ctx is None:
            ctx = getattr(self, "_ctx", None)
        if ctx is None:
            raise TypeError("reload() missing required argument: 'ctx' (BusList is not bound to a context)")
        return cast("BusList[TRecord]", super().reload(ctx))

    @overload
    def reload_with(
        self,
        ctx: BusReplayContext,
        *,
        inplace: bool = False,
    ) -> "BusList[TRecord]": ...

    @overload
    def reload_with(
        self,
        ctx: None = None,
        *,
        inplace: bool = False,
    ) -> "BusList[TRecord]": ...

    def reload_with(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        inplace: bool = False,
    ) -> "BusList[TRecord]":
        """Reload with optional in-place mutation.

        中文: reload 的底层实现, 可选择 inplace=True 直接更新当前对象内容.
        English: Underlying reload implementation; can mutate current instance when inplace=True.

        Args:
            ctx:
                中文: 需要提供 ctx.bus.messages/events/lifecycle 等接口.
                English: Context providing ctx.bus.* clients.
            inplace:
                中文: True 时原对象会被更新(保持同一实例), False 时返回新列表.
                English: When True, mutate this instance; otherwise return a new list.

        Raises:
            NonReplayableTraceError: plan 缺失或含不可重放步骤(如 sort(key=callable), where(predicate)).
        """
        if ctx is None:
            ctx = getattr(self, "_ctx", None)
        if ctx is None:
            raise TypeError("reload_with() missing required argument: 'ctx' (BusList is not bound to a context)")

        if self._plan is None:
            raise NonReplayableTraceError("reload is unavailable when no replayable plan exists")

        refreshed = self._replay_plan(ctx, self._plan)
        if not inplace:
            with suppress(Exception):
                refreshed._ctx = ctx
                refreshed._cache_valid = True
            return refreshed

        # In-place refresh: mutate current instance to hold latest items, keep same plan.
        _apply_reload_inplace_basic(self, refreshed, ctx)

        # Append a trace marker for observability (plan stays the same query expression).
        with suppress(Exception):
            self._trace = self._trace + (BusOp(name="reload", params={}, at=time.time()),)

        return self

    async def reload_with_async(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        inplace: bool = False,
    ) -> "BusList[TRecord]":
        """异步版本的 reload_with，使用 asyncio.to_thread 包装同步调用。
        
        Note: 底层 ZMQ socket 是同步的，此方法通过线程池实现非阻塞。
        
        Args:
            ctx: 上下文，提供 ctx.bus.* 客户端
            inplace: True 时原对象会被更新，False 时返回新列表
            
        Returns:
            刷新后的 BusList
        """
        return cast(
            "BusList[TRecord]",
            await super().reload_with_async(
                ctx,
                inplace=inplace,
            ),
        )

    @overload
    def watch(
        self,
        ctx: BusReplayContext,
        *,
        bus: Optional[str] = None,
        debounce_ms: float = 0.0,
    ) -> "BusListWatcher[TRecord]": ...

    @overload
    def watch(
        self,
        ctx: None = None,
        *,
        bus: Optional[str] = None,
        debounce_ms: float = 0.0,
    ) -> "BusListWatcher[TRecord]": ...

    def watch(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        bus: Optional[str] = None,
        debounce_ms: float = 0.0,
    ) -> "BusListWatcher[TRecord]":
        """Create a watcher for this query.

        中文: 基于当前可重放 plan 创建 watcher, 用于监听 bus 变化并触发 subscribe 回调.
        English: Create a watcher based on the replayable plan for change notifications.

        Args:
            ctx:
                中文: 需要提供 ctx.bus.* 以及(在插件进程内)必要的 IPC 能力.
                English: Context providing bus clients and (in plugin process) IPC capability.
            bus:
                中文: 手动指定 bus 类型("messages"/"events"/"lifecycle"). 默认从 plan 自动推断.
                English: Override bus name; otherwise inferred from the plan.
            debounce_ms:
                中文: 监听去抖(毫秒). >0 时会合并短时间内多次 bus change, 降低 reload 频率.
                English: Debounce window in milliseconds. When >0, coalesce bursts of bus changes.

        Note:
            - watcher 需要 replayable plan; where(predicate) 这类不可重放操作会报错.
        """
        if ctx is None:
            ctx = getattr(self, "_ctx", None)
        if ctx is None:
            raise TypeError("watch() missing required argument: 'ctx' (BusList is not bound to a context)")
        from plugin.core.bus.watchers import BusListWatcher

        return BusListWatcher(self, ctx, bus=bus, debounce_ms=debounce_ms)


from plugin.core.bus.watchers import BusListDelta, BusListWatcher, list_Subscription, list_subscription  # noqa: E402

__all__ = [
    "BusChange",
    "BusChangeOp",
    "BusFilter",
    "BusFilterError",
    "BusList",
    "BusListDelta",
    "BusListWatcher",
    "BusOp",
    "BusRecord",
    "BusReplayContext",
    "NonReplayableTraceError",
    "dispatch_bus_change",
    "list_subscription",
    "list_Subscription",
]

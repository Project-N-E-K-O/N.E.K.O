from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import re
import time
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Final,
    Literal,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeVar,
    TYPE_CHECKING,
    overload,
    Union,
)

if TYPE_CHECKING:
    from plugin.sdk.bus.events import EventList
    from plugin.sdk.bus.lifecycle import LifecycleList
    from plugin.sdk.bus.memory import MemoryList
    from plugin.sdk.bus.messages import MessageList

_WATCHER_REGISTRY: Dict[str, "BusListWatcher[Any]"] = {}
_WATCHER_REGISTRY_LOCK = None
try:
    import threading

    _WATCHER_REGISTRY_LOCK = threading.Lock()
except Exception:
    _WATCHER_REGISTRY_LOCK = None


def dispatch_bus_change(*, sub_id: str, bus: str, op: str, delta: Optional[Dict[str, Any]] = None) -> None:
    sid = str(sub_id).strip()
    if not sid:
        return
    if _WATCHER_REGISTRY_LOCK is not None:
        with _WATCHER_REGISTRY_LOCK:
            w = _WATCHER_REGISTRY.get(sid)
    else:
        w = _WATCHER_REGISTRY.get(sid)
    if w is None:
        return
    try:
        w._on_remote_change(bus=str(bus), op=str(op), delta=dict(delta or {}))
    except Exception:
        return


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


class BusHubProtocol(Protocol):
    messages: _MessageClientProto
    events: _EventClientProto
    lifecycle: _LifecycleClientProto
    memory: _MemoryClientProto


class BusReplayContext(Protocol):
    bus: BusHubProtocol


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


@dataclass(frozen=True)
class BusRecord:
    kind: str
    type: str
    timestamp: Optional[float]
    plugin_id: Optional[str] = None
    source: Optional[str] = None
    priority: int = 0
    content: Optional[str] = None
    metadata: Dict[str, Any] = None  # type: ignore[assignment]
    raw: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", {} if self.metadata is None else dict(self.metadata))
        object.__setattr__(self, "raw", {} if self.raw is None else dict(self.raw))

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
class BusFilterResult(Generic[TRecord]):
    ok: bool
    value: Optional["BusList[TRecord]"] = None
    error: Optional[Exception] = None


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


@dataclass(frozen=True)
class BinaryNode(TraceNode):
    left: TraceNode
    right: TraceNode

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["kind"] = "binary"
        base["left"] = self.left.dump()
        base["right"] = self.right.dump()
        return base

    def explain(self) -> str:
        return f"({self.left.explain()}) {self.op} ({self.right.explain()})"


class BusList(Generic[TRecord]):
    def __init__(
        self,
        items: Sequence[TRecord],
        *,
        ctx: Optional[BusReplayContext] = None,
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[TraceNode] = None,
        fast_mode: bool = False,
    ):
        self._items: List[TRecord] = list(items)
        self._ctx: Optional[BusReplayContext] = ctx
        self._fast_mode = bool(fast_mode)
        self._trace: Tuple[BusOp, ...] = tuple(trace or ()) if not self._fast_mode else ()
        self._plan: Optional[TraceNode] = plan if not self._fast_mode else None
        self._cache_valid: bool = True

    def _is_lazy_mode(self) -> bool:
        return self._ctx is not None and self._plan is not None and not self._fast_mode

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
            try:
                setattr(self, "plugin_id", getattr(refreshed, "plugin_id"))
            except Exception:
                pass
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
    def fast_mode(self) -> bool:
        return self._fast_mode

    @property
    def trace(self) -> Tuple[BusOp, ...]:
        return self._trace

    def trace_dump(self) -> List[Dict[str, Any]]:
        """Dump the recorded query trace.

        中文: 返回 trace 的可序列化版本, 用于调试/展示链式调用做了什么.
        English: Return a serializable trace describing the chained operations.

        Note:
            - fast_mode=True 时 trace 为空.
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
            - None: when plan is missing (e.g. fast_mode=True)
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
        if self._fast_mode:
            return ()
        p = params if isinstance(params, dict) else {}
        return self._trace + (BusOp(name=name, params=p, at=time.time()),)

    def _add_plan_unary(self, op: str, params: Optional[Dict[str, Any]] = None) -> Optional[TraceNode]:
        if self._fast_mode:
            return None
        if self._plan is None:
            return None
        p = params if isinstance(params, dict) else {}
        return UnaryNode(op=op, params=p, at=time.time(), child=self._plan)

    def _add_plan_binary(self, op: str, right: "BusList[TRecord]", params: Optional[Dict[str, Any]] = None) -> Optional[TraceNode]:
        if self._fast_mode:
            return None
        if self._plan is None or right._plan is None:
            return None
        p = params if isinstance(params, dict) else {}
        return BinaryNode(op=op, params=p, at=time.time(), left=self._plan, right=right._plan)

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
            "fast_mode": self._fast_mode,
        }
        if hasattr(self, "plugin_id"):
            kwargs["plugin_id"] = getattr(self, "plugin_id")
        try:
            out = self.__class__(items, **kwargs)  # type: ignore[call-arg]
        except TypeError:
            kwargs.pop("plugin_id", None)
            out = self.__class__(items, **kwargs)  # type: ignore[call-arg]
        try:
            if getattr(self, "_ctx", None) is not None and plan is not None and not getattr(self, "_fast_mode", False):
                out._cache_valid = False  # type: ignore[attr-defined]
        except Exception:
            pass
        return out

    def _dedupe_key(self, item: TRecord) -> Tuple[str, Any]:
        for attr in ("message_id", "event_id", "lifecycle_id", "trace_id"):
            try:
                v = getattr(item, attr, None)
            except Exception:
                v = None
            if isinstance(v, str) and v:
                return (attr, v)

        raw = None
        try:
            raw = getattr(item, "raw", None)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            for k in ("message_id", "event_id", "lifecycle_id", "trace_id"):
                v = raw.get(k)
                if isinstance(v, str) and v:
                    return (k, v)

        try:
            dumped = item.dump()
            fp = tuple(sorted((str(k), repr(v)) for k, v in dumped.items()))
            return ("dump", fp)
        except Exception:
            return ("object", id(item))

    def _sort_value(self, v: Any) -> Tuple[int, Any]:
        if v is None:
            return (2, "")
        if isinstance(v, (int, float)):
            return (0, v)
        return (1, str(v))

    def _get_sort_field(self, item: TRecord, field: str) -> Any:
        try:
            return getattr(item, field)
        except Exception:
            pass

        raw = None
        try:
            raw = getattr(item, "raw", None)
        except Exception:
            raw = None
        if isinstance(raw, dict) and field in raw:
            return raw.get(field)

        try:
            dumped = item.dump()
            return dumped.get(field)
        except Exception:
            return None

    def _get_field(self, item: Any, field: str) -> Any:
        try:
            return getattr(item, field)
        except Exception:
            pass
        raw = None
        try:
            raw = getattr(item, "raw", None)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            return raw.get(field)
        try:
            dumped = item.dump()
            if isinstance(dumped, dict):
                return dumped.get(field)
        except Exception:
            pass
        return None

    def _cast_value(self, v: Any, cast: Optional[str]) -> Any:
        if cast is None:
            return v
        c = str(cast).strip().lower()
        if c in ("int", "i"):
            try:
                return int(str(v).strip())
            except Exception:
                return 0
        if c in ("float", "f"):
            try:
                return float(str(v).strip())
            except Exception:
                return 0.0
        if c in ("str", "s"):
            try:
                return "" if v is None else str(v)
            except Exception:
                return ""
        return v

    def merge(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(f"Cannot merge different bus list types: {type(self).__name__} + {type(other).__name__}")

        merged: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in list(self._items) + list(other._items):
            key = self._dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        if self._is_lazy_mode() or other._is_lazy_mode():
            left_len = len(self._items)
            right_len = len(other._items)
        else:
            left_len = len(self)
            right_len = len(other)

        trace = self._add_trace("merge", {"left": left_len, "right": right_len})
        plan = self._add_plan_binary("merge", other, {"left": left_len, "right": right_len})
        out = self._construct(merged, trace, plan)
        out._invalidate_cache()
        return out

    def __add__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.merge(other)

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
                    self._sort_value(self._cast_value(self._get_sort_field(x, f), cast))
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

    def sorted(
        self,
        *,
        by: Optional[Union[str, Sequence[str]]] = None,
        key: Optional[Callable[[TRecord], Any]] = None,
        cast: Optional[str] = None,
        reverse: bool = False,
    ) -> "BusList[TRecord]":
        return self.sort(by=by, key=key, cast=cast, reverse=reverse)

    def intersection(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(
                f"Cannot intersect different bus list types: {type(self).__name__} & {type(other).__name__}"
            )

        other_keys = {other._dedupe_key(x) for x in other._items}
        kept: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in self._items:
            key = self._dedupe_key(item)
            if key not in other_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)

        if self._is_lazy_mode() or other._is_lazy_mode():
            left_len = len(self._items)
            right_len = len(other._items)
        else:
            left_len = len(self)
            right_len = len(other)
        trace = self._add_trace("intersection", {"left": left_len, "right": right_len})
        plan = self._add_plan_binary("intersection", other, {"left": left_len, "right": right_len})
        out = self._construct(kept, trace, plan)
        out._invalidate_cache()
        return out

    def intersect(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.intersection(other)

    def __and__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.intersection(other)

    def difference(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        if type(self) is not type(other):
            raise TypeError(
                f"Cannot diff different bus list types: {type(self).__name__} - {type(other).__name__}"
            )

        other_keys = {other._dedupe_key(x) for x in other._items}
        kept: List[TRecord] = []
        seen: set[Tuple[str, Any]] = set()
        for item in self._items:
            key = self._dedupe_key(item)
            if key in other_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)

        if self._is_lazy_mode() or other._is_lazy_mode():
            left_len = len(self._items)
            right_len = len(other._items)
        else:
            left_len = len(self)
            right_len = len(other)
        trace = self._add_trace("difference", {"left": left_len, "right": right_len})
        plan = self._add_plan_binary("difference", other, {"left": left_len, "right": right_len})
        out = self._construct(kept, trace, plan)
        out._invalidate_cache()
        return out

    def subtract(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.difference(other)

    def __sub__(self, other: "BusList[TRecord]") -> "BusList[TRecord]":
        return self.difference(other)

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

        def _re_ok(field: str, pattern: Optional[str], value: Optional[str]) -> bool:
            if pattern is None:
                return True
            if value is None:
                return False
            try:
                return re.search(pattern, value) is not None
            except re.error as e:
                if strict:
                    raise BusFilterError(f"Invalid regex for {field}: {pattern!r}") from e
                return False

        def _match(x: BusRecord) -> bool:
            if flt.kind is not None and x.kind != flt.kind:
                return False
            if flt.type is not None and x.type != flt.type:
                return False
            if flt.plugin_id is not None and x.plugin_id != flt.plugin_id:
                return False
            if flt.source is not None and x.source != flt.source:
                return False
            if not _re_ok("kind_re", flt.kind_re, x.kind):
                return False
            if not _re_ok("type_re", flt.type_re, x.type):
                return False
            if not _re_ok("plugin_id_re", flt.plugin_id_re, x.plugin_id):
                return False
            if not _re_ok("source_re", flt.source_re, x.source):
                return False
            if not _re_ok("content_re", flt.content_re, x.content):
                return False
            if flt.priority_min is not None:
                try:
                    if int(x.priority) < int(flt.priority_min):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid priority_min: {flt.priority_min!r}") from e
                    return False
            if flt.since_ts is not None:
                ts = x.timestamp
                try:
                    if ts is None or float(ts) < float(flt.since_ts):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid since_ts: {flt.since_ts!r}") from e
                    return False
            if flt.until_ts is not None:
                ts = x.timestamp
                try:
                    if ts is None or float(ts) > float(flt.until_ts):
                        return False
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Invalid until_ts: {flt.until_ts!r}") from e
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

    def where_in(self, field: str, values: Sequence[Any]) -> "BusList[TRecord]":
        vs = list(values)

        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = [item for item in self._items if self._get_field(item, field) in vs]
        trace = self._add_trace("where_in", {"field": field, "values": vs})
        plan = self._add_plan_unary("where_in", {"field": field, "values": vs})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_eq(self, field: str, value: Any) -> "BusList[TRecord]":
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = [item for item in self._items if self._get_field(item, field) == value]
        trace = self._add_trace("where_eq", {"field": field, "value": value})
        plan = self._add_plan_unary("where_eq", {"field": field, "value": value})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_contains(self, field: str, value: str) -> "BusList[TRecord]":
        needle = str(value)
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._get_field(item, field)
                if v is None:
                    continue
                try:
                    if needle in str(v):
                        items.append(item)
                except Exception:
                    continue
        trace = self._add_trace("where_contains", {"field": field, "value": needle})
        plan = self._add_plan_unary("where_contains", {"field": field, "value": needle})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_regex(self, field: str, pattern: str, *, strict: bool = True) -> "BusList[TRecord]":
        pat = str(pattern)
        try:
            compiled = re.compile(pat)
        except re.error as e:
            if strict:
                raise BusFilterError(f"Invalid regex for where_regex({field}): {pat!r}") from e
            compiled = None

        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._get_field(item, field)
                if v is None:
                    continue
                s = str(v)
                try:
                    if compiled is not None and compiled.search(s) is not None:
                        items.append(item)
                except Exception as e:
                    if strict:
                        raise BusFilterError(f"Regex match failed for where_regex({field})") from e
                    continue

        trace = self._add_trace("where_regex", {"field": field, "pattern": pat, "strict": strict})
        plan = self._add_plan_unary("where_regex", {"field": field, "pattern": pat, "strict": strict})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_gt(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._cast_value(self._get_field(item, field), cast)
                try:
                    if v > target:
                        items.append(item)
                except Exception:
                    continue
        trace = self._add_trace("where_gt", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_gt", {"field": field, "value": value, "cast": cast})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_ge(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._cast_value(self._get_field(item, field), cast)
                try:
                    if v >= target:
                        items.append(item)
                except Exception:
                    continue
        trace = self._add_trace("where_ge", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_ge", {"field": field, "value": value, "cast": cast})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_lt(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._cast_value(self._get_field(item, field), cast)
                try:
                    if v < target:
                        items.append(item)
                except Exception:
                    continue
        trace = self._add_trace("where_lt", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_lt", {"field": field, "value": value, "cast": cast})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def where_le(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        if self._is_lazy_mode():
            items = list(self._items)
        else:
            items = []
            for item in self._items:
                v = self._cast_value(self._get_field(item, field), cast)
                try:
                    if v <= target:
                        items.append(item)
                except Exception:
                    continue
        trace = self._add_trace("where_le", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_le", {"field": field, "value": value, "cast": cast})
        out = self._construct(items, trace, plan)
        out._invalidate_cache()
        return out

    def try_filter(self, flt: Optional[BusFilter] = None, **kwargs: Any) -> BusFilterResult[TRecord]:
        try:
            value = self.filter(flt, strict=True, **kwargs)
            return BusFilterResult(ok=True, value=value, error=None)
        except BusFilterError as e:
            return BusFilterResult(ok=False, value=None, error=e)

    def where(self, predicate: Callable[[TRecord], bool]) -> "BusList[TRecord]":
        """Filter using an arbitrary Python predicate.

        中文: 使用任意 Python 函数 predicate 过滤记录, 返回新 BusList.
        English: Filter with an arbitrary Python predicate callable.

        Warning:
            - where(predicate) 由于 predicate 不可序列化/不可重放, reload()/watch() 无法重放这一步.
            - 如果需要可重放过滤, 优先使用 where_eq/where_in/where_* 等结构化方法.
        """
        if self._is_lazy_mode():
            raise NonReplayableTraceError("lazy list cannot use where(predicate); use where_in/where_eq/... instead")
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
        def _as_eager(lst: "BusList[TRecord]") -> "BusList[TRecord]":
            try:
                lst._ctx = None  # type: ignore[assignment]
                lst._cache_valid = True  # type: ignore[assignment]
            except Exception:
                pass
            return lst

        if isinstance(plan, GetNode):
            bus = str(plan.params.get("bus") or "").strip()
            params = dict(plan.params.get("params") or {})
            if bus == "messages":
                return _as_eager(ctx.bus.messages.get(**params))
            if bus == "events":
                return _as_eager(ctx.bus.events.get(**params))
            if bus == "lifecycle":
                return _as_eager(ctx.bus.lifecycle.get(**params))
            raise NonReplayableTraceError(f"Unknown bus for reload: {bus!r}")

        if isinstance(plan, UnaryNode):
            base = _as_eager(self._replay_plan(ctx, plan.child))
            if plan.op == "filter":
                p = dict(plan.params)
                strict = bool(p.pop("strict", True))
                return base.filter(strict=strict, **p)
            if plan.op == "limit":
                return base.limit(int(plan.params.get("n", 0)))
            if plan.op == "sort":
                if plan.params.get("key") is not None:
                    raise NonReplayableTraceError("reload cannot replay sort(key=callable); use sort(by=...) only")
                return base.sort(
                    by=plan.params.get("by"),
                    cast=plan.params.get("cast"),
                    reverse=bool(plan.params.get("reverse", False)),
                )
            if plan.op == "where_in":
                return base.where_in(str(plan.params.get("field")), list(plan.params.get("values") or []))
            if plan.op == "where_eq":
                return base.where_eq(str(plan.params.get("field")), plan.params.get("value"))
            if plan.op == "where_contains":
                return base.where_contains(str(plan.params.get("field")), str(plan.params.get("value") or ""))
            if plan.op == "where_regex":
                return base.where_regex(
                    str(plan.params.get("field")),
                    str(plan.params.get("pattern") or ""),
                    strict=bool(plan.params.get("strict", True)),
                )
            if plan.op == "where_gt":
                return base.where_gt(
                    str(plan.params.get("field")),
                    plan.params.get("value"),
                    cast=plan.params.get("cast"),
                )
            if plan.op == "where_ge":
                return base.where_ge(
                    str(plan.params.get("field")),
                    plan.params.get("value"),
                    cast=plan.params.get("cast"),
                )
            if plan.op == "where_lt":
                return base.where_lt(
                    str(plan.params.get("field")),
                    plan.params.get("value"),
                    cast=plan.params.get("cast"),
                )
            if plan.op == "where_le":
                return base.where_le(
                    str(plan.params.get("field")),
                    plan.params.get("value"),
                    cast=plan.params.get("cast"),
                )
            if plan.op == "where":
                raise NonReplayableTraceError("reload cannot replay where(predicate); use where_in/where_eq/... instead")
            raise NonReplayableTraceError(f"Unknown unary op for reload: {plan.op!r}")

        if isinstance(plan, BinaryNode):
            left = _as_eager(self._replay_plan(ctx, plan.left))
            right = _as_eager(self._replay_plan(ctx, plan.right))
            if plan.op == "merge":
                return left + right
            if plan.op == "intersection":
                return left & right
            if plan.op == "difference":
                return left - right
            raise NonReplayableTraceError(f"Unknown binary op for reload: {plan.op!r}")

        raise NonReplayableTraceError(f"Unknown plan node type: {type(plan).__name__}")

    @overload
    def reload(self, ctx: BusReplayContext) -> "BusList[TRecord]": ...

    @overload
    def reload(self, ctx: None = None) -> "BusList[TRecord]": ...

    def reload(self, ctx: Optional[BusReplayContext] = None) -> "BusList[TRecord]":
        """Replay the recorded plan against live bus data.

        中文: 使用可重放 plan 重新从 bus 拉取数据并应用同样的链式操作, 返回最新 BusList.
        English: Reload from bus by replaying the stored plan and operations.

        Requirements:
            - 必须是 replayable plan (通常由 get()/filter()/where_*/sort(by=...)/limit() 组合产生).
            - fast_mode=True 或 plan 缺失时不可用.
        """
        if ctx is None:
            ctx = getattr(self, "_ctx", None)
        if ctx is None:
            raise TypeError("reload() missing required argument: 'ctx' (BusList is not bound to a context)")
        return self.reload_with(ctx)

    @overload
    def reload_with(self, ctx: BusReplayContext, *, inplace: bool = False) -> "BusList[TRecord]": ...

    @overload
    def reload_with(self, ctx: None = None, *, inplace: bool = False) -> "BusList[TRecord]": ...

    def reload_with(self, ctx: Optional[BusReplayContext] = None, *, inplace: bool = False) -> "BusList[TRecord]":
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
            raise NonReplayableTraceError("reload is unavailable when fast_mode=True or plan is missing")

        refreshed = self._replay_plan(ctx, self._plan)
        if not inplace:
            try:
                refreshed._ctx = ctx  # type: ignore[assignment]
                refreshed._cache_valid = True  # type: ignore[assignment]
            except Exception:
                pass
            return refreshed

        # In-place refresh: mutate current instance to hold latest items, keep same plan.
        self._items = list(refreshed.dump_records())
        self._ctx = ctx
        self._cache_valid = True
        if hasattr(self, "plugin_id") and hasattr(refreshed, "plugin_id"):
            try:
                setattr(self, "plugin_id", getattr(refreshed, "plugin_id"))
            except Exception:
                pass

        # Append a trace marker for observability (plan stays the same query expression).
        if not self._fast_mode:
            try:
                self._trace = self._trace + (BusOp(name="reload", params={}, at=time.time()),)
            except Exception:
                pass

        return self

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
            - watcher 需要 replayable plan; fast_mode 或 where(predicate) 这类不可重放会报错.
        """
        if ctx is None:
            ctx = getattr(self, "_ctx", None)
        if ctx is None:
            raise TypeError("watch() missing required argument: 'ctx' (BusList is not bound to a context)")
        return BusListWatcher(self, ctx, bus=bus, debounce_ms=debounce_ms)


@dataclass(frozen=True)
class BusListDelta(Generic[TRecord]):
    kind: BusChangeOp
    added: Tuple[TRecord, ...]
    removed: Tuple[DedupeKey, ...]
    current: BusList[TRecord]


class BusListWatcher(Generic[TRecord]):
    def __init__(
        self,
        lst: BusList[TRecord],
        ctx: BusReplayContext,
        *,
        bus: Optional[str] = None,
        debounce_ms: float = 0.0,
    ):
        self._list = lst
        self._ctx = ctx
        self._debounce_ms = float(debounce_ms or 0.0)

        if self._list._plan is None:
            raise NonReplayableTraceError("watcher requires a replayable plan; build list via get()/filter()/where_*/sort(by=...)")

        inferred = self._infer_bus(self._list._plan)
        self._bus = str(bus).strip() if isinstance(bus, str) and bus.strip() else inferred
        if self._bus not in ("messages", "events", "lifecycle"):
            raise NonReplayableTraceError(f"watcher cannot infer bus type from plan: {self._bus!r}")

        self._lock = None
        try:
            import threading

            self._lock = threading.Lock()
        except Exception:
            self._lock = None

        self._callbacks: List[Tuple[Callable[[BusListDelta[TRecord]], None], Tuple[BusChangeOp, ...]]] = []
        self._unsub: Optional[Callable[[], None]] = None
        self._sub_id: Optional[str] = None
        self._last_keys: set[DedupeKey] = {self._list._dedupe_key(x) for x in self._list.dump_records()}

        self._debounce_timer: Any = None
        self._pending_op: Optional[str] = None
        self._pending_payload: Optional[Dict[str, Any]] = None

    def _schedule_tick(self, op: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if self._debounce_ms <= 0:
            self._tick(op, payload)
            return

        try:
            import threading

            delay = max(0.0, self._debounce_ms / 1000.0)
            if self._lock is not None:
                with self._lock:
                    self._pending_op = str(op)
                    self._pending_payload = dict(payload or {}) if isinstance(payload, dict) else None
                    t = self._debounce_timer
                    self._debounce_timer = None
            else:
                self._pending_op = str(op)
                self._pending_payload = dict(payload or {}) if isinstance(payload, dict) else None
                t = self._debounce_timer
                self._debounce_timer = None

            try:
                if t is not None:
                    t.cancel()
            except Exception:
                pass

            def _fire() -> None:
                if self._lock is not None:
                    with self._lock:
                        pending = self._pending_op
                        pending_payload = self._pending_payload
                        self._pending_op = None
                        self._pending_payload = None
                        self._debounce_timer = None
                else:
                    pending = self._pending_op
                    pending_payload = self._pending_payload
                    self._pending_op = None
                    self._pending_payload = None
                    self._debounce_timer = None

                try:
                    self._tick(str(pending or "change"), pending_payload)
                except Exception:
                    return

            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            if self._lock is not None:
                with self._lock:
                    self._debounce_timer = timer
            else:
                self._debounce_timer = timer
            timer.start()
        except Exception:
            self._tick(op)

    def _make_injected_callback(self, fn: Callable[..., None]) -> Callable[[BusListDelta[TRecord]], None]:
        try:
            sig = inspect.signature(fn)
        except Exception:
            return fn  # type: ignore[return-value]

        params = list(sig.parameters.values())
        if len(params) == 1 and params[0].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return fn  # type: ignore[return-value]

        def _dump_record(rec: Any) -> Any:
            if hasattr(rec, "dump") and callable(getattr(rec, "dump")):
                try:
                    return rec.dump()
                except Exception:
                    return rec
            return rec

        def _wrapped(delta: BusListDelta[TRecord]) -> None:
            try:
                added = delta.added
                removed = delta.removed
                current = delta.current
                mapping: Dict[str, Any] = {
                    "delta": delta,
                    "d": delta,
                    "list": current,
                    "current": current,
                    "buslist": current,
                    "added": added,
                    "removed": removed,
                    "length": len(added),
                    "len": len(added),
                    "count": len(added),
                    "kind": delta.kind,
                    "op": delta.kind,
                    "quickdump": tuple(_dump_record(x) for x in added),
                }

                kwargs: Dict[str, Any] = {}
                for p in params:
                    if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                        continue
                    if p.name in mapping:
                        kwargs[p.name] = mapping[p.name]
                    elif p.default is inspect._empty:
                        # Unknown required parameter name: fallback to delta-only call.
                        fn(delta)
                        return

                if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
                    fn(**kwargs)
                else:
                    fn(**kwargs)
            except Exception:
                fn(delta)

        return _wrapped

    def _infer_bus(self, plan: TraceNode) -> str:
        if isinstance(plan, GetNode):
            return str(plan.params.get("bus") or "").strip()
        if isinstance(plan, UnaryNode):
            return self._infer_bus(plan.child)
        if isinstance(plan, BinaryNode):
            # prefer left branch
            return self._infer_bus(plan.left)
        return ""

    def subscribe(
        self,
        *,
        on: Union[BusChangeOp, Sequence[BusChangeOp]] = ("add",),
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        if isinstance(on, str):
            rules: Tuple[BusChangeOp, ...] = (on,)
        else:
            rules = tuple(on)

        def _decorator(fn: Callable[..., None]) -> Callable[..., None]:
            wrapped = self._make_injected_callback(fn)
            if self._lock is not None:
                with self._lock:
                    self._callbacks.append((wrapped, rules))
            else:
                self._callbacks.append((wrapped, rules))
            return fn

        return _decorator

    def start(self) -> "BusListWatcher[TRecord]":
        if self._unsub is not None or self._sub_id is not None:
            return self

        # Plugin process: register with server via IPC and wait for BUS change push.
        if getattr(self._ctx, "_plugin_comm_queue", None) is not None and hasattr(self._ctx, "_send_request_and_wait"):
            res = self._ctx._send_request_and_wait(
                method_name="bus_subscribe",
                request_type="BUS_SUBSCRIBE",
                request_data={
                    "bus": self._bus,
                    "rules": ["add", "del", "change"],
                    "deliver": "delta",
                    "plan": self._list.trace_tree_dump(),
                },
                timeout=5.0,
                wrap_result=True,
            )
            sub_id = None
            if isinstance(res, dict):
                sub_id = res.get("sub_id")
            if not isinstance(sub_id, str) or not sub_id:
                raise RuntimeError("BUS_SUBSCRIBE failed: missing sub_id")
            self._sub_id = sub_id
            if _WATCHER_REGISTRY_LOCK is not None:
                with _WATCHER_REGISTRY_LOCK:
                    _WATCHER_REGISTRY[sub_id] = self  # type: ignore[assignment]
            else:
                _WATCHER_REGISTRY[sub_id] = self  # type: ignore[assignment]
            return self

        # In-process fallback: subscribe to core state hub.
        from plugin.core.state import state

        def _on_event(_op: str, _payload: Dict[str, Any]) -> None:
            try:
                self._schedule_tick(_op, _payload)
            except Exception:
                return

        self._unsub = state.bus_change_hub.subscribe(self._bus, _on_event)
        return self

    def stop(self) -> None:
        if self._sub_id is not None:
            sid = self._sub_id
            self._sub_id = None
            if _WATCHER_REGISTRY_LOCK is not None:
                with _WATCHER_REGISTRY_LOCK:
                    _WATCHER_REGISTRY.pop(sid, None)
            else:
                _WATCHER_REGISTRY.pop(sid, None)

            try:
                if getattr(self._ctx, "_plugin_comm_queue", None) is not None and hasattr(self._ctx, "_send_request_and_wait"):
                    self._ctx._send_request_and_wait(
                        method_name="bus_unsubscribe",
                        request_type="BUS_UNSUBSCRIBE",
                        request_data={"bus": self._bus, "sub_id": sid},
                        timeout=3.0,
                        wrap_result=True,
                    )
            except Exception:
                pass
            return

        if self._unsub is None:
            return
        try:
            self._unsub()
        finally:
            self._unsub = None

    def _on_remote_change(self, *, bus: str, op: str, delta: Dict[str, Any]) -> None:
        # Server push arrived in plugin process; use reload+diff as source of truth.
        _ = (bus,)
        try:
            self._schedule_tick(op, delta)
        except Exception:
            return

    def _extract_plan_ops(self) -> Optional[List[Tuple[str, Dict[str, Any]]]]:
        plan = getattr(self._list, "_plan", None)
        if plan is None:
            return None
        if isinstance(plan, BinaryNode):
            return None

        ops: List[Tuple[str, Dict[str, Any]]] = []
        node: TraceNode = plan
        while isinstance(node, UnaryNode):
            ops.append((str(node.op), dict(node.params) if isinstance(node.params, dict) else {}))
            node = node.child
        if not isinstance(node, GetNode):
            return None
        ops.reverse()
        return ops

    def _infer_bus(self, plan: TraceNode) -> str:
        if isinstance(plan, GetNode):
            return str(plan.params.get("bus") or "").strip()
        if isinstance(plan, UnaryNode):
            return self._infer_bus(plan.child)
        if isinstance(plan, BinaryNode):
            left = self._infer_bus(plan.left)
            right = self._infer_bus(plan.right)
            if left and right and left != right:
                raise NonReplayableTraceError(f"watcher requires same bus on both sides: {left!r} vs {right!r}")
            return left or right
        return ""

    def _apply_ops_local(self, base_items: List[TRecord], ops: List[Tuple[str, Dict[str, Any]]]) -> Optional[BusList[TRecord]]:
        try:
            base = self._list._construct(base_items, self._list._trace, self._list._plan)
        except Exception:
            base = BusList(base_items)

        lst: BusList[TRecord] = base
        for op, params in ops:
            if op == "filter":
                p = dict(params)
                strict = bool(p.pop("strict", True))
                lst = lst.filter(strict=strict, **p)
                continue
            if op == "limit":
                lst = lst.limit(int(params.get("n", 0)))
                continue
            if op == "sort":
                if params.get("key") is not None:
                    return None
                lst = lst.sort(
                    by=params.get("by"),
                    cast=params.get("cast"),
                    reverse=bool(params.get("reverse", False)),
                )
                continue
            if op == "where_in":
                lst = lst.where_in(str(params.get("field")), list(params.get("values") or []))
                continue
            if op == "where_eq":
                lst = lst.where_eq(str(params.get("field")), params.get("value"))
                continue
            if op == "where_contains":
                lst = lst.where_contains(str(params.get("field")), str(params.get("value") or ""))
                continue
            if op == "where_regex":
                lst = lst.where_regex(
                    str(params.get("field")),
                    str(params.get("pattern") or ""),
                    strict=bool(params.get("strict", True)),
                )
                continue
            if op == "where_gt":
                lst = lst.where_gt(str(params.get("field")), params.get("value"), cast=params.get("cast"))
                continue
            if op == "where_ge":
                lst = lst.where_ge(str(params.get("field")), params.get("value"), cast=params.get("cast"))
                continue
            if op == "where_lt":
                lst = lst.where_lt(str(params.get("field")), params.get("value"), cast=params.get("cast"))
                continue
            if op == "where_le":
                lst = lst.where_le(str(params.get("field")), params.get("value"), cast=params.get("cast"))
                continue
            if op == "where":
                return None
        return lst

    def _record_from_raw(self, raw: Dict[str, Any]) -> Optional[TRecord]:
        try:
            if self._bus == "messages":
                from plugin.sdk.bus.messages import MessageRecord

                return MessageRecord.from_raw(raw)  # type: ignore[return-value]
            if self._bus == "events":
                from plugin.sdk.bus.events import EventRecord

                return EventRecord.from_raw(raw)  # type: ignore[return-value]
            if self._bus == "lifecycle":
                from plugin.sdk.bus.lifecycle import LifecycleRecord

                return LifecycleRecord.from_raw(raw)  # type: ignore[return-value]
        except Exception:
            return None
        return None

    def _try_incremental(self, op: str, payload: Optional[Dict[str, Any]]) -> Optional[BusList[TRecord]]:
        if not isinstance(payload, dict) or not payload:
            return None

        ops = self._extract_plan_ops()
        if ops is None:
            return None

        current_items = self._list.dump_records()
        base_items: List[TRecord] = list(current_items)

        if str(op) == "add" and isinstance(payload.get("record"), dict):
            rec = self._record_from_raw(payload.get("record"))
            if rec is None:
                return None
            base_items.append(rec)
            return self._apply_ops_local(base_items, ops)

        if str(op) == "del":
            rid: Optional[str] = None
            attr: Optional[str] = None
            if self._bus == "messages":
                rid = payload.get("message_id") if isinstance(payload.get("message_id"), str) else None
                attr = "message_id"
            elif self._bus == "events":
                rid = payload.get("event_id") if isinstance(payload.get("event_id"), str) else None
                attr = "event_id"
            elif self._bus == "lifecycle":
                rid = payload.get("lifecycle_id") if isinstance(payload.get("lifecycle_id"), str) else None
                attr = "lifecycle_id"

            if not rid or not attr:
                return None

            # If query has limit, deletion may require pulling another record to fill; fall back.
            if any(op_name == "limit" for op_name, _ in ops):
                return None

            kept: List[TRecord] = []
            for x in base_items:
                k = self._list._dedupe_key(x)
                if k == (attr, rid):
                    continue
                kept.append(x)
            return self._apply_ops_local(kept, ops)

        return None

    def _tick(self, op: str, payload: Optional[Dict[str, Any]] = None) -> None:
        refreshed = None
        try:
            refreshed = self._try_incremental(op, payload)
        except Exception:
            refreshed = None
        if refreshed is None:
            refreshed = self._list.reload(self._ctx)
        new_items = refreshed.dump_records()
        new_keys: set[DedupeKey] = {self._list._dedupe_key(x) for x in new_items}

        added_items: List[TRecord] = []
        for x in new_items:
            k = self._list._dedupe_key(x)
            if k not in self._last_keys:
                added_items.append(x)

        removed_keys: Tuple[DedupeKey, ...] = tuple(k for k in self._last_keys if k not in new_keys)

        fired: List[BusChangeOp] = []
        if added_items:
            fired.append("add")
        if removed_keys:
            fired.append("del")
        if added_items or removed_keys:
            fired.append("change")

        if not fired:
            self._last_keys = new_keys
            self._list = refreshed
            return

        kind: BusChangeOp = op if op in ("add", "del", "change") else "change"
        delta = BusListDelta(kind=kind, added=tuple(added_items), removed=removed_keys, current=refreshed)

        if self._lock is not None:
            with self._lock:
                callbacks = list(self._callbacks)
        else:
            callbacks = list(self._callbacks)

        for fn, rules in callbacks:
            if any(r in fired for r in rules):
                try:
                    fn(delta)
                except Exception:
                    continue

        self._last_keys = new_keys
        self._list = refreshed


def list_Subscription(
    watcher: BusListWatcher[TRecord],
    *,
    on: Union[BusChangeOp, Sequence[BusChangeOp]] = ("add",),
) -> Callable[[Callable[[BusListDelta[TRecord]], None]], Callable[[BusListDelta[TRecord]], None]]:
    return watcher.subscribe(on=on)

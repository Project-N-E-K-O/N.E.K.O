from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque
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

import uuid

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

_BUS_LATEST_REV: Dict[str, int] = {
    "messages": 0,
    "events": 0,
    "lifecycle": 0,
}
_BUS_LATEST_REV_LOCK = _WATCHER_REGISTRY_LOCK

_BUS_RECENT_DELTAS: Dict[str, "deque[tuple[int, str, Dict[str, Any]]]" ] = {}

_BUS_CHANGE_LISTENERS: Dict[str, "list[Callable[[str, str, Dict[str, Any]], None]]"] = {
    "messages": [],
    "events": [],
    "lifecycle": [],
}


def register_bus_change_listener(bus: str, fn: "Callable[[str, str, Dict[str, Any]], None]") -> Callable[[], None]:
    b = str(bus).strip()
    if b not in _BUS_CHANGE_LISTENERS:
        raise ValueError(f"invalid bus: {bus!r}")
    if not callable(fn):
        raise ValueError("listener must be callable")
    if _BUS_LATEST_REV_LOCK is not None:
        with _BUS_LATEST_REV_LOCK:
            _BUS_CHANGE_LISTENERS[b].append(fn)
    else:
        _BUS_CHANGE_LISTENERS[b].append(fn)

    def _unsub() -> None:
        try:
            if _BUS_LATEST_REV_LOCK is not None:
                with _BUS_LATEST_REV_LOCK:
                    lst = _BUS_CHANGE_LISTENERS.get(b)
                    if lst is not None:
                        try:
                            lst.remove(fn)
                        except ValueError:
                            pass
            else:
                lst = _BUS_CHANGE_LISTENERS.get(b)
                if lst is not None:
                    try:
                        lst.remove(fn)
                    except ValueError:
                        pass
        except Exception:
            return

    return _unsub

_BUS_REV_SUB_ID: Dict[str, str] = {}


class _BusRevSink:
    def _on_remote_change(self, *, bus: str, op: str, delta: Dict[str, Any]) -> None:
        _ = (bus, op, delta)
        return


def _ensure_bus_rev_subscription(ctx: Any, bus: str) -> None:
    """确保总线订阅(内部函数,ctx可以是PluginContext或PluginContextProtocol)"""
    b = str(bus).strip()
    if b not in ("messages", "events", "lifecycle"):
        return
    if getattr(ctx, "_plugin_comm_queue", None) is None or not hasattr(ctx, "_send_request_and_wait"):
        return

    try:
        if _BUS_LATEST_REV_LOCK is not None:
            with _BUS_LATEST_REV_LOCK:
                sid0 = _BUS_REV_SUB_ID.get(b)
        else:
            sid0 = _BUS_REV_SUB_ID.get(b)
        if isinstance(sid0, str) and sid0:
            return
    except Exception:
        pass

    try:
        res = ctx._send_request_and_wait(
            method_name="bus_subscribe",
            request_type="BUS_SUBSCRIBE",
            request_data={
                "bus": b,
                "rules": ["add", "del", "change"],
                "deliver": "delta",
                "plan": None,
            },
            timeout=5.0,
            wrap_result=True,
        )
    except Exception:
        return

    sub_id = None
    cur_rev = None
    try:
        if isinstance(res, dict):
            sub_id = res.get("sub_id")
            cur_rev = res.get("rev")
    except Exception:
        sub_id = None

    if not isinstance(sub_id, str) or not sub_id:
        return

    sink = _BusRevSink()
    if _WATCHER_REGISTRY_LOCK is not None:
        with _WATCHER_REGISTRY_LOCK:
            _WATCHER_REGISTRY[sub_id] = sink  # type: ignore[assignment]
            _BUS_REV_SUB_ID[b] = sub_id
    else:
        _WATCHER_REGISTRY[sub_id] = sink  # type: ignore[assignment]
        _BUS_REV_SUB_ID[b] = sub_id

    if cur_rev is not None:
        try:
            r = int(cur_rev)
        except Exception:
            r = None
        if r is not None:
            if _BUS_LATEST_REV_LOCK is not None:
                with _BUS_LATEST_REV_LOCK:
                    prev = int(_BUS_LATEST_REV.get(b, 0))
                    if r > prev:
                        _BUS_LATEST_REV[b] = r
            else:
                prev = int(_BUS_LATEST_REV.get(b, 0))
                if r > prev:
                    _BUS_LATEST_REV[b] = r


def dispatch_bus_change(*, sub_id: str, bus: str, op: str, delta: Optional[Dict[str, Any]] = None) -> None:
    sid = str(sub_id).strip()
    if not sid:
        return
    try:
        b = str(bus).strip()
        d = dict(delta or {})
        rev = d.get("rev")
        if b in _BUS_LATEST_REV and rev is not None:
            try:
                r = int(rev)
            except Exception:
                r = None
            if r is not None:
                try:
                    from collections import deque

                    if _BUS_LATEST_REV_LOCK is not None:
                        with _BUS_LATEST_REV_LOCK:
                            q = _BUS_RECENT_DELTAS.get(b)
                            if q is None:
                                q = deque(maxlen=512)
                                _BUS_RECENT_DELTAS[b] = q
                            q.append((r, str(op), dict(d)))
                    else:
                        q = _BUS_RECENT_DELTAS.get(b)
                        if q is None:
                            q = deque(maxlen=512)
                            _BUS_RECENT_DELTAS[b] = q
                        q.append((r, str(op), dict(d)))
                except Exception:
                    pass
                if _BUS_LATEST_REV_LOCK is not None:
                    with _BUS_LATEST_REV_LOCK:
                        prev = int(_BUS_LATEST_REV.get(b, 0))
                        if r > prev:
                            _BUS_LATEST_REV[b] = r
                else:
                    prev = int(_BUS_LATEST_REV.get(b, 0))
                    if r > prev:
                        _BUS_LATEST_REV[b] = r
    except Exception:
        pass
    if _WATCHER_REGISTRY_LOCK is not None:
        with _WATCHER_REGISTRY_LOCK:
            w = _WATCHER_REGISTRY.get(sid)
    else:
        w = _WATCHER_REGISTRY.get(sid)
    if w is None:
        try:
            b2 = str(bus).strip()
            d2 = dict(delta or {})
            if b2 in _BUS_CHANGE_LISTENERS:
                if _BUS_LATEST_REV_LOCK is not None:
                    with _BUS_LATEST_REV_LOCK:
                        listeners = list(_BUS_CHANGE_LISTENERS.get(b2, []))
                else:
                    listeners = list(_BUS_CHANGE_LISTENERS.get(b2, []))
                for fn in listeners:
                    try:
                        fn(b2, str(op), d2)
                    except Exception:
                        continue
        except Exception:
            pass
        return
    try:
        w._on_remote_change(bus=str(bus), op=str(op), delta=dict(delta or {}))
    except Exception:
        return

    try:
        b2 = str(bus).strip()
        d2 = dict(delta or {})
        if b2 in _BUS_CHANGE_LISTENERS:
            if _BUS_LATEST_REV_LOCK is not None:
                with _BUS_LATEST_REV_LOCK:
                    listeners = list(_BUS_CHANGE_LISTENERS.get(b2, []))
            else:
                listeners = list(_BUS_CHANGE_LISTENERS.get(b2, []))
            for fn in listeners:
                try:
                    fn(b2, str(op), d2)
                except Exception:
                    continue
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
    metadata: Dict[str, Any] = None  # type: ignore[assignment]
    raw: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Only copy if None, avoid unnecessary dict() calls
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})
        if self.raw is None:
            object.__setattr__(self, "raw", {})

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


def _collect_get_nodes_fast(node: "TraceNode") -> List["GetNode"]:
    """Module-level function to collect GetNodes from a plan tree (iterative, faster)."""
    result: List["GetNode"] = []
    stack: List["TraceNode"] = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, GetNode):
            result.append(n)
        elif isinstance(n, UnaryNode):
            stack.append(n.child)
        elif isinstance(n, BinaryNode):
            stack.append(n.left)
            stack.append(n.right)
    return result


def _serialize_plan_fast(node: "TraceNode") -> Optional[Dict[str, Any]]:
    """Module-level function to serialize a plan tree to dict (iterative for common cases)."""
    try:
        if isinstance(node, GetNode):
            return {"kind": "get", "op": "get", "params": dict(node.params or {})}
        if isinstance(node, UnaryNode):
            child = _serialize_plan_fast(node.child)
            if child is None:
                return None
            return {
                "kind": "unary",
                "op": str(node.op),
                "params": dict(node.params or {}),
                "child": child,
            }
        if isinstance(node, BinaryNode):
            left = _serialize_plan_fast(node.left)
            right = _serialize_plan_fast(node.right)
            if left is None or right is None:
                return None
            return {
                "kind": "binary",
                "op": str(node.op),
                "params": dict(node.params or {}),
                "left": left,
                "right": right,
            }
    except Exception:
        return None
    return None


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
        # Optimization: avoid unnecessary list() copy if items is already a list
        self._items: List[TRecord] = items if isinstance(items, list) else list(items)
        self._ctx: Optional[BusReplayContext] = ctx
        self._fast_mode = bool(fast_mode)
        self._trace: Tuple[BusOp, ...] = tuple(trace or ()) if not self._fast_mode else ()
        self._plan: Optional[TraceNode] = plan if not self._fast_mode else None
        self._cache_valid: bool = True
        self._reload_cursor_ts: Optional[float] = None
        self._incremental_seed: Optional[Dict[str, Any]] = None
        self._incremental_base_items: Optional[List[Any]] = None
        self._last_seen_bus_rev: Optional[int] = None
        self._incremental_cached_items: Optional[List[Any]] = None
        self._incremental_fast_hits: int = 0

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
        cache: Dict[Any, Any] = {}

        def _as_eager(lst: Any) -> Any:
            try:
                lst._ctx = None  # type: ignore[assignment]
                lst._cache_valid = True  # type: ignore[assignment]
            except Exception:
                pass
            return lst

        def _freeze(x: Any) -> Any:
            try:
                if isinstance(x, dict):
                    return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
                if isinstance(x, (list, tuple)):
                    return tuple(_freeze(v) for v in x)
                if isinstance(x, set):
                    return tuple(sorted(_freeze(v) for v in x))
                if isinstance(x, (str, int, float, bool, type(None))):
                    return x
                return repr(x)
            except Exception:
                return repr(x)

        def _cache_key(node: TraceNode) -> Any:
            try:
                if isinstance(node, GetNode):
                    bus = str(node.params.get("bus") or "")
                    params = dict(node.params.get("params") or {})
                    return ("get", bus, _freeze(params))
                if isinstance(node, UnaryNode):
                    return ("unary", str(node.op), _freeze(dict(node.params or {})), _cache_key(node.child))
                if isinstance(node, BinaryNode):
                    return (
                        "binary",
                        str(node.op),
                        _freeze(dict(node.params or {})),
                        _cache_key(node.left),
                        _cache_key(node.right),
                    )
            except Exception:
                return ("node", id(node))
            return ("node", id(node))

        def _replay(node: TraceNode) -> "BusList[TRecord]":
            key = _cache_key(node)
            cached = cache.get(key)
            if cached is not None:
                return cached

            # Push down a chain of filter(...) into the underlying GetNode when possible.
            # This reduces IPC payload for full reload.
            if isinstance(node, UnaryNode) and node.op == "filter":
                filters: List[Dict[str, Any]] = []
                cur: TraceNode = node
                while isinstance(cur, UnaryNode) and cur.op == "filter":
                    filters.append(dict(cur.params or {}))
                    cur = cur.child

                if isinstance(cur, GetNode):
                    bus = str(cur.params.get("bus") or "").strip()
                    base_params = dict(cur.params.get("params") or {})

                    if bus in {"messages", "events", "lifecycle"}:
                        # Merge the whole filter dict (method B) and let the server do filtering.
                        merged_filter: Dict[str, Any] = {}
                        strict_val: bool = True
                        for fp in reversed(filters):
                            p = dict(fp)
                            if "strict" in p:
                                try:
                                    strict_val = bool(p.get("strict"))
                                except Exception:
                                    strict_val = strict_val
                            _ = p.pop("strict", None)
                            merged_filter.update({k: v for k, v in p.items() if v is not None})

                        merged = dict(base_params)
                        merged["filter"] = dict(merged_filter) if merged_filter else None
                        merged["strict"] = bool(strict_val)
                        out0 = _replay(GetNode(op="get", params={"bus": bus, "params": merged}, at=cur.at))
                        cache[key] = out0
                        return out0

            if isinstance(node, GetNode):
                bus = str(node.params.get("bus") or "").strip()
                params = dict(node.params.get("params") or {})
                if bus == "messages":
                    out = _as_eager(ctx.bus.messages.get(**params))
                elif bus == "events":
                    out = _as_eager(ctx.bus.events.get(**params))
                elif bus == "lifecycle":
                    out = _as_eager(ctx.bus.lifecycle.get(**params))
                else:
                    raise NonReplayableTraceError(f"Unknown bus for reload: {bus!r}")
                cache[key] = out
                return out

            if isinstance(node, UnaryNode):
                base = _as_eager(_replay(node.child))
                if node.op == "filter":
                    p = dict(node.params)
                    strict = bool(p.pop("strict", True))
                    out = base.filter(strict=strict, **p)
                    cache[key] = out
                    return out
                if node.op == "limit":
                    out = base.limit(int(node.params.get("n", 0)))
                    cache[key] = out
                    return out
                if node.op == "sort":
                    if node.params.get("key") is not None:
                        raise NonReplayableTraceError("reload cannot replay sort(key=callable); use sort(by=...) only")
                    out = base.sort(
                        by=node.params.get("by"),
                        cast=node.params.get("cast"),
                        reverse=bool(node.params.get("reverse", False)),
                    )
                    cache[key] = out
                    return out
                if node.op == "where_in":
                    out = base.where_in(str(node.params.get("field")), list(node.params.get("values") or []))
                    cache[key] = out
                    return out
                if node.op == "where_eq":
                    out = base.where_eq(str(node.params.get("field")), node.params.get("value"))
                    cache[key] = out
                    return out
                if node.op == "where_contains":
                    out = base.where_contains(str(node.params.get("field")), str(node.params.get("value") or ""))
                    cache[key] = out
                    return out
                if node.op == "where_regex":
                    out = base.where_regex(
                        str(node.params.get("field")),
                        str(node.params.get("pattern") or ""),
                        strict=bool(node.params.get("strict", True)),
                    )
                    cache[key] = out
                    return out
                if node.op == "where_gt":
                    out = base.where_gt(
                        str(node.params.get("field")),
                        node.params.get("value"),
                        cast=node.params.get("cast"),
                    )
                    cache[key] = out
                    return out
                if node.op == "where_ge":
                    out = base.where_ge(
                        str(node.params.get("field")),
                        node.params.get("value"),
                        cast=node.params.get("cast"),
                    )
                    cache[key] = out
                    return out
                if node.op == "where_lt":
                    out = base.where_lt(
                        str(node.params.get("field")),
                        node.params.get("value"),
                        cast=node.params.get("cast"),
                    )
                    cache[key] = out
                    return out
                if node.op == "where_le":
                    out = base.where_le(
                        str(node.params.get("field")),
                        node.params.get("value"),
                        cast=node.params.get("cast"),
                    )
                    cache[key] = out
                    return out
                if node.op == "where":
                    raise NonReplayableTraceError("reload cannot replay where(predicate); use where_in/where_eq/... instead")
                raise NonReplayableTraceError(f"Unknown unary op for reload: {node.op!r}")

            if isinstance(node, BinaryNode):
                left = _as_eager(_replay(node.left))
                right = _as_eager(_replay(node.right))
                if node.op == "merge":
                    out = left + right
                elif node.op == "intersection":
                    out = left & right
                elif node.op == "difference":
                    out = left - right
                else:
                    raise NonReplayableTraceError(f"Unknown binary op for reload: {node.op!r}")
                cache[key] = out
                return out

            raise NonReplayableTraceError(f"Unknown plan node type: {type(node).__name__}")
        return _replay(plan)

    @overload
    def reload(self, ctx: BusReplayContext) -> "BusList[TRecord]": ...

    @overload
    def reload(self, ctx: None = None) -> "BusList[TRecord]": ...

    def reload(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        incremental: bool = False,
    ) -> "BusList[TRecord]":
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
        return self.reload_with(ctx, incremental=bool(incremental))

    @overload
    def reload_with(
        self,
        ctx: BusReplayContext,
        *,
        inplace: bool = False,
        incremental: bool = False,
    ) -> "BusList[TRecord]": ...

    @overload
    def reload_with(
        self,
        ctx: None = None,
        *,
        inplace: bool = False,
        incremental: bool = False,
    ) -> "BusList[TRecord]": ...

    def reload_with(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        inplace: bool = False,
        incremental: bool = False,
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
            raise NonReplayableTraceError("reload is unavailable when fast_mode=True or plan is missing")


        def _message_plane_replay(*, bus: str, plan: TraceNode, timeout: float = 1.0) -> Optional[List[Dict[str, Any]]]:
            try:
                import time as _time
                import json as _json
                import os as _os
                import ormsgpack as _ormsgpack
                try:
                    import zmq as _zmq
                except Exception:
                    _zmq = None
                if _zmq is None:
                    return None
                from plugin.settings import MESSAGE_PLANE_ZMQ_RPC_ENDPOINT

                plan_dict = _serialize_plan_fast(plan)
                if plan_dict is None:
                    return None
                endpoint = str(MESSAGE_PLANE_ZMQ_RPC_ENDPOINT)
                if not endpoint:
                    return None

                # Use thread-local socket to avoid multi-threading issues
                sock = None
                try:
                    import threading
                    tls = getattr(ctx, "_mp_replay_tls", None)
                    if tls is None:
                        tls = threading.local()
                        setattr(ctx, "_mp_replay_tls", tls)
                    sock = getattr(tls, "sock", None)
                except Exception:
                    # No threading support, fall back to ctx-level socket
                    try:
                        sock = getattr(ctx, "_mp_replay_sock", None)
                    except Exception:
                        sock = None
                
                if sock is None:
                    zctx = _zmq.Context.instance()
                    sock = zctx.socket(_zmq.DEALER)
                    try:
                        ident = f"mp-replay:{getattr(ctx, 'plugin_id', '')}:{int(_time.time() * 1000)}".encode("utf-8")
                        sock.setsockopt(_zmq.IDENTITY, ident)
                    except Exception:
                        pass
                    try:
                        sock.setsockopt(_zmq.LINGER, 0)
                    except Exception:
                        pass
                    sock.connect(endpoint)
                    
                    # Store in thread-local storage if available
                    try:
                        import threading
                        tls = getattr(ctx, "_mp_replay_tls", None)
                        if tls is not None:
                            tls.sock = sock
                        else:
                            setattr(ctx, "_mp_replay_sock", sock)
                    except Exception:
                        try:
                            setattr(ctx, "_mp_replay_sock", sock)
                        except Exception:
                            pass

                req_id = f"replay:{getattr(ctx, 'plugin_id', '')}:{uuid.uuid4()}"
                # Performance knob: allow full reload to request light records from message_plane
                # (omit payload) to reduce IPC and JSON processing cost.
                # Env: NEKO_BUSLIST_RELOAD_FULL_LIGHT=1
                try:
                    light_mode = str(_os.getenv("NEKO_BUSLIST_RELOAD_FULL_LIGHT", "0")).strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
                except Exception:
                    light_mode = False
                req = {
                    "v": 1,
                    "op": "bus.replay",
                    "req_id": req_id,
                    "from_plugin": getattr(ctx, "plugin_id", ""),
                    "args": {"store": str(bus), "plan": plan_dict, "light": bool(light_mode)},
                }
                try:
                    raw = _ormsgpack.packb(req)
                except Exception:
                    raw = _json.dumps(req, ensure_ascii=False).encode("utf-8")
                try:
                    sock.send(raw, flags=0)
                except Exception:
                    return None

                deadline = _time.time() + max(0.0, float(timeout))
                while True:
                    remaining = deadline - _time.time()
                    if remaining <= 0:
                        return None
                    try:
                        if sock.poll(timeout=int(remaining * 1000), flags=_zmq.POLLIN) == 0:
                            continue
                    except Exception:
                        return None
                    try:
                        resp_raw = sock.recv(flags=0)
                    except Exception:
                        return None
                    resp = None
                    try:
                        resp = _ormsgpack.unpackb(resp_raw)
                    except Exception:
                        try:
                            resp = _json.loads(resp_raw.decode("utf-8"))
                        except Exception:
                            resp = None
                    if not isinstance(resp, dict):
                        continue
                    if resp.get("req_id") != req_id:
                        continue
                    if not resp.get("ok"):
                        return None
                    result = resp.get("result")
                    if not isinstance(result, dict):
                        return None
                    items = result.get("items")
                    if not isinstance(items, list):
                        return None
                    out: List[Dict[str, Any]] = []
                    for ev in items:
                        if isinstance(ev, dict):
                            out.append(ev)
                    return out
            except Exception:
                return None

        # Full reload: prefer server-side replay in message_plane to avoid expensive Python-side list ops.
        if not incremental:
            get_nodes = _collect_get_nodes_fast(self._plan)
            if get_nodes:
                seed0 = get_nodes[0]
                seed_bus = str(seed0.params.get("bus") or "").strip()
                if seed_bus in ("messages", "events", "lifecycle"):
                    same_bus = True
                    for gn in get_nodes[1:]:
                        if str(gn.params.get("bus") or "").strip() != seed_bus:
                            same_bus = False
                            break
                    if same_bus:
                        # Timeout comes from the original GetNode when present.
                        timeout_s = 1.0
                        try:
                            params0 = dict(seed0.params.get("params") or {})
                            t0 = params0.get("timeout")
                            if t0 is not None:
                                timeout_s = float(t0)
                        except Exception:
                            timeout_s = 1.0

                        items = _message_plane_replay(bus=seed_bus, plan=self._plan, timeout=timeout_s)
                        if items is not None:
                            try:
                                if seed_bus == "messages":
                                    from plugin.sdk.bus.messages import MessageRecord

                                    recs = []
                                    for ev in items:
                                        idx = ev.get("index")
                                        payload = ev.get("payload")
                                        if isinstance(idx, dict):
                                            recs.append(MessageRecord.from_index(idx, payload if isinstance(payload, dict) else None))
                                        elif isinstance(payload, dict):
                                            recs.append(MessageRecord.from_raw(payload))
                                elif seed_bus == "events":
                                    from plugin.sdk.bus.events import EventRecord

                                    recs = []
                                    for ev in items:
                                        idx = ev.get("index")
                                        payload = ev.get("payload")
                                        if isinstance(idx, dict):
                                            recs.append(EventRecord.from_index(idx, payload if isinstance(payload, dict) else None))
                                        elif isinstance(payload, dict):
                                            recs.append(EventRecord.from_raw(payload))
                                else:
                                    from plugin.sdk.bus.lifecycle import LifecycleRecord

                                    recs = []
                                    for ev in items:
                                        idx = ev.get("index")
                                        payload = ev.get("payload")
                                        if isinstance(idx, dict):
                                            recs.append(LifecycleRecord.from_index(idx, payload if isinstance(payload, dict) else None))
                                        elif isinstance(payload, dict):
                                            recs.append(LifecycleRecord.from_raw(payload))
                            except Exception:
                                recs = []  # type: ignore[assignment]

                            if inplace:
                                self._items = list(recs)  # type: ignore[list-item]
                                self._ctx = ctx
                                self._cache_valid = True
                                return self
                            out = self.__class__(
                                list(recs),  # type: ignore[arg-type]
                                ctx=ctx,
                                trace=self._trace,
                                plan=self._plan,
                                fast_mode=self._fast_mode,
                            )
                            return out

        # Experimental: incremental reload for replayable plans.
        # Strategy:
        # - Identify the underlying GetNode seed (bus + params).
        # - Maintain a local snapshot of the GetNode result (bounded by max_count) as base.
        # - On incremental reload, fetch only delta (since_ts) from bus, merge into base snapshot.
        # - Replay the full plan locally against the updated base snapshot.
        if incremental:
            def _seed_key(bus: str, params: Dict[str, Any]) -> Dict[str, Any]:
                # since_ts is runtime cursor and should not be part of identity.
                p = dict(params)
                p.pop("since_ts", None)
                return {"bus": bus, "params": p}

            get_nodes = _collect_get_nodes_fast(self._plan)
            if not get_nodes:
                # No GetNode => cannot incrementally fetch delta
                refreshed = self._replay_plan(ctx, self._plan)
            else:
                # Require all GetNodes to be equivalent (same bus+params excluding since_ts)
                seed0 = get_nodes[0]
                seed_bus = str(seed0.params.get("bus") or "").strip()
                seed_params0 = dict(seed0.params.get("params") or {})
                seed_id0 = _seed_key(seed_bus, seed_params0)
                ok = True
                for gn in get_nodes[1:]:
                    b = str(gn.params.get("bus") or "").strip()
                    pp = dict(gn.params.get("params") or {})
                    if _seed_key(b, pp) != seed_id0:
                        ok = False
                        break
                if not ok or not seed_bus:
                    # Fallback: full replay (may hit bus multiple times)
                    refreshed = self._replay_plan(ctx, self._plan)
                else:
                    try:
                        _ensure_bus_rev_subscription(ctx, seed_bus)
                    except Exception:
                        pass
                    latest_rev: Optional[int] = None
                    try:
                        if _BUS_LATEST_REV_LOCK is not None:
                            with _BUS_LATEST_REV_LOCK:
                                latest_rev = int(_BUS_LATEST_REV.get(seed_bus, 0))
                        else:
                            latest_rev = int(_BUS_LATEST_REV.get(seed_bus, 0))
                    except Exception:
                        latest_rev = None

                    def _collect_source_filters(node: TraceNode) -> set[str]:
                        out: set[str] = set()
                        try:
                            if isinstance(node, UnaryNode):
                                if str(node.op) == "filter" and isinstance(node.params, dict):
                                    v = node.params.get("source")
                                    if isinstance(v, str) and v:
                                        out.add(v)
                                out |= _collect_source_filters(node.child)
                            elif isinstance(node, BinaryNode):
                                out |= _collect_source_filters(node.left)
                                out |= _collect_source_filters(node.right)
                        except Exception:
                            return out
                        return out

                    def _deltas_affect_query(*, bus: str, from_rev: int, to_rev: int, plan: TraceNode) -> bool:
                        # Minimal heuristic: if plan filters by source, only treat deltas whose record.source matches.
                        sources = _collect_source_filters(plan)
                        if len(sources) != 1:
                            return True
                        src = next(iter(sources))
                        try:
                            if _BUS_LATEST_REV_LOCK is not None:
                                with _BUS_LATEST_REV_LOCK:
                                    q = list(_BUS_RECENT_DELTAS.get(bus, []))
                            else:
                                q = list(_BUS_RECENT_DELTAS.get(bus, []))
                        except Exception:
                            return True

                        found = False
                        for r, op0, d0 in q:
                            if int(r) <= int(from_rev) or int(r) > int(to_rev):
                                continue
                            found = True
                            rec = None
                            if isinstance(d0, dict):
                                rec = d0.get("record")
                            if isinstance(rec, dict):
                                if str(rec.get("source") or "") == str(src):
                                    return True
                            else:
                                # Lightweight delta may omit record. If it carries a source hint, use it;
                                # otherwise be conservative.
                                if isinstance(d0, dict) and isinstance(d0.get("source"), str):
                                    if str(d0.get("source") or "") == str(src):
                                        return True
                                    continue
                                return True
                        # If we have no delta history for this rev window, be conservative.
                        if not found and int(to_rev) != int(from_rev):
                            return True
                        return False

                    if (
                        latest_rev is not None
                        and self._last_seen_bus_rev is not None
                        and int(latest_rev) == int(self._last_seen_bus_rev)
                        and self._incremental_seed == seed_id0
                    ):
                        cached = getattr(self, "_incremental_cached_items", None)
                        if cached is None:
                            # No cached materialization available; fall back to normal incremental path.
                            pass
                        else:
                            try:
                                self._incremental_fast_hits = int(getattr(self, "_incremental_fast_hits", 0)) + 1
                            except Exception:
                                self._incremental_fast_hits = 1
                            if inplace:
                                self._items = list(cached)  # type: ignore[list-item]
                                self._ctx = ctx
                                self._cache_valid = True
                                return self
                            out = self.__class__(
                                list(cached),  # type: ignore[arg-type]
                                ctx=ctx,
                                trace=self._trace,
                                plan=self._plan,
                                fast_mode=self._fast_mode,
                            )
                            try:
                                out._reload_cursor_ts = self._reload_cursor_ts  # type: ignore[attr-defined]
                                out._incremental_seed = self._incremental_seed  # type: ignore[attr-defined]
                                out._incremental_base_items = self._incremental_base_items  # type: ignore[attr-defined]
                                out._last_seen_bus_rev = self._last_seen_bus_rev  # type: ignore[attr-defined]
                                out._incremental_cached_items = list(cached)  # type: ignore[attr-defined]
                                out._incremental_fast_hits = int(getattr(self, "_incremental_fast_hits", 0))  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            return out

                    # If bus rev changed but the changes do not affect this query, we can still fast-hit.
                    if (
                        latest_rev is not None
                        and self._last_seen_bus_rev is not None
                        and int(latest_rev) > int(self._last_seen_bus_rev)
                        and self._incremental_seed == seed_id0
                        and self._incremental_cached_items is not None
                    ):
                        try:
                            affects = _deltas_affect_query(
                                bus=seed_bus,
                                from_rev=int(self._last_seen_bus_rev),
                                to_rev=int(latest_rev),
                                plan=self._plan,
                            )
                        except Exception:
                            affects = True
                        if not affects:
                            self._last_seen_bus_rev = int(latest_rev)
                            try:
                                self._incremental_fast_hits = int(getattr(self, "_incremental_fast_hits", 0)) + 1
                            except Exception:
                                self._incremental_fast_hits = 1
                            cached2 = list(self._incremental_cached_items)
                            if inplace:
                                self._items = list(cached2)  # type: ignore[list-item]
                                self._ctx = ctx
                                self._cache_valid = True
                                return self
                            out2 = self.__class__(
                                cached2,  # type: ignore[arg-type]
                                ctx=ctx,
                                trace=self._trace,
                                plan=self._plan,
                                fast_mode=self._fast_mode,
                            )
                            try:
                                out2._reload_cursor_ts = self._reload_cursor_ts  # type: ignore[attr-defined]
                                out2._incremental_seed = self._incremental_seed  # type: ignore[attr-defined]
                                out2._incremental_base_items = self._incremental_base_items  # type: ignore[attr-defined]
                                out2._last_seen_bus_rev = self._last_seen_bus_rev  # type: ignore[attr-defined]
                                out2._incremental_cached_items = list(cached2)  # type: ignore[attr-defined]
                                out2._incremental_fast_hits = int(getattr(self, "_incremental_fast_hits", 0))  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            return out2

                    # Ensure base snapshot exists for this seed; if seed changed, reset snapshot/cursor.
                    if self._incremental_seed != seed_id0 or self._incremental_base_items is None:
                        # Initialize base snapshot by doing a normal replay once.
                        base_list = self._replay_plan(ctx, seed0)
                        self._incremental_seed = seed_id0
                        self._incremental_base_items = list(base_list.dump_records())
                        if latest_rev is not None:
                            self._last_seen_bus_rev = int(latest_rev)
                        # Update cursor from base snapshot
                        try:
                            max_ts0: Optional[float] = None
                            for d in base_list.dump():
                                if not isinstance(d, dict):
                                    continue
                                ts0 = (
                                    parse_iso_timestamp(d.get("time"))
                                    or parse_iso_timestamp(d.get("timestamp"))
                                    or parse_iso_timestamp(d.get("received_at"))
                                )
                                if ts0 is None:
                                    continue
                                if max_ts0 is None or ts0 > max_ts0:
                                    max_ts0 = ts0
                            if max_ts0 is not None:
                                self._reload_cursor_ts = max_ts0
                        except Exception:
                            pass

                    # Fetch delta from bus
                    delta_params = dict(seed_params0)
                    if self._reload_cursor_ts is not None:
                        delta_params["since_ts"] = float(self._reload_cursor_ts)

                    delta_list = None
                    try:
                        if seed_bus == "messages":
                            delta_list = ctx.bus.messages.get(**delta_params)
                        elif seed_bus == "events":
                            delta_list = ctx.bus.events.get(**delta_params)
                        elif seed_bus == "lifecycle":
                            delta_list = ctx.bus.lifecycle.get(**delta_params)
                        else:
                            delta_list = None
                    except Exception:
                        delta_list = None

                    # Merge delta into base snapshot
                    if delta_list is not None and self._incremental_base_items is not None:
                        base_items = list(self._incremental_base_items)
                        base_keys: set[Any] = set()
                        try:
                            for it in base_items:
                                base_keys.add(self._dedupe_key(it))
                        except Exception:
                            base_keys = set()

                        try:
                            for rec in delta_list.dump_records():
                                k = self._dedupe_key(cast(Any, rec))
                                if k in base_keys:
                                    continue
                                base_keys.add(k)
                                base_items.append(rec)
                        except Exception:
                            pass

                        # Base snapshot should respect max_count of the seed get.
                        try:
                            mc0 = seed_params0.get("max_count")
                            if mc0 is not None:
                                n0 = int(mc0)
                                if n0 > 0 and len(base_items) > n0:
                                    base_items = base_items[-n0:]
                        except Exception:
                            pass

                        self._incremental_base_items = list(base_items)

                        # Update cursor from delta
                        try:
                            max_ts: Optional[float] = None
                            for d in delta_list.dump():
                                if not isinstance(d, dict):
                                    continue
                                ts = (
                                    parse_iso_timestamp(d.get("time"))
                                    or parse_iso_timestamp(d.get("timestamp"))
                                    or parse_iso_timestamp(d.get("received_at"))
                                )
                                if ts is None:
                                    continue
                                if max_ts is None or ts > max_ts:
                                    max_ts = ts
                            if max_ts is not None:
                                self._reload_cursor_ts = max_ts
                        except Exception:
                            pass

                    # Replay full plan locally using base snapshot as the GetNode seed.
                    seed_bus_now = seed_bus
                    items_any = list(self._incremental_base_items or [])

                    def _make_seed_buslist() -> Any:
                        # Construct a generic BusList with the base snapshot as eager items.
                        return BusList(items_any, ctx=None, trace=None, plan=None, fast_mode=True)  # type: ignore[name-defined]

                    def _replay_local(node: TraceNode) -> Any:
                        if isinstance(node, GetNode):
                            # Return base snapshot list
                            return _make_seed_buslist()
                        if isinstance(node, UnaryNode):
                            base = _replay_local(node.child)
                            if node.op == "filter":
                                p = dict(node.params)
                                strict = bool(p.pop("strict", True))
                                return base.filter(strict=strict, **p)
                            if node.op == "limit":
                                return base.limit(int(node.params.get("n", 0)))
                            if node.op == "sort":
                                if node.params.get("key") is not None:
                                    raise NonReplayableTraceError(
                                        "incremental reload cannot replay sort(key=callable); use sort(by=...) only"
                                    )
                                return base.sort(
                                    by=node.params.get("by"),
                                    cast=node.params.get("cast"),
                                    reverse=bool(node.params.get("reverse", False)),
                                )
                            if node.op == "where_in":
                                return base.where_in(str(node.params.get("field")), list(node.params.get("values") or []))
                            if node.op == "where_eq":
                                return base.where_eq(str(node.params.get("field")), node.params.get("value"))
                            if node.op == "where_contains":
                                return base.where_contains(str(node.params.get("field")), str(node.params.get("value") or ""))
                            if node.op == "where_regex":
                                return base.where_regex(
                                    str(node.params.get("field")),
                                    str(node.params.get("pattern") or ""),
                                    strict=bool(node.params.get("strict", True)),
                                )
                            if node.op == "where_gt":
                                return base.where_gt(str(node.params.get("field")), node.params.get("value"), cast=node.params.get("cast"))
                            if node.op == "where_ge":
                                return base.where_ge(str(node.params.get("field")), node.params.get("value"), cast=node.params.get("cast"))
                            if node.op == "where_lt":
                                return base.where_lt(str(node.params.get("field")), node.params.get("value"), cast=node.params.get("cast"))
                            if node.op == "where_le":
                                return base.where_le(str(node.params.get("field")), node.params.get("value"), cast=node.params.get("cast"))
                            if node.op == "where":
                                raise NonReplayableTraceError(
                                    "incremental reload cannot replay where(predicate); use where_in/where_eq/... instead"
                                )
                            raise NonReplayableTraceError(f"Unknown unary op for incremental reload: {node.op!r}")
                        if isinstance(node, BinaryNode):
                            left = _replay_local(node.left)
                            right = _replay_local(node.right)
                            if node.op == "merge":
                                return left + right
                            if node.op == "intersection":
                                return left & right
                            if node.op == "difference":
                                return left - right
                            raise NonReplayableTraceError(f"Unknown binary op for incremental reload: {node.op!r}")
                        raise NonReplayableTraceError(f"Unknown plan node type: {type(node).__name__}")

                    refreshed = _replay_local(self._plan)
                    # Materialize result records
                    out_items = list(refreshed.dump_records()) if hasattr(refreshed, "dump_records") else list(refreshed)
                    refreshed = self.__class__(
                        out_items,  # type: ignore[arg-type]
                        ctx=ctx,
                        trace=self._trace,
                        plan=self._plan,
                        fast_mode=self._fast_mode,
                    )
                    try:
                        refreshed._reload_cursor_ts = self._reload_cursor_ts  # type: ignore[attr-defined]
                        refreshed._incremental_seed = self._incremental_seed  # type: ignore[attr-defined]
                        refreshed._incremental_base_items = self._incremental_base_items  # type: ignore[attr-defined]
                        if latest_rev is not None:
                            refreshed._last_seen_bus_rev = int(latest_rev)  # type: ignore[attr-defined]
                            self._last_seen_bus_rev = int(latest_rev)
                        refreshed._incremental_cached_items = list(out_items)  # type: ignore[attr-defined]
                        self._incremental_cached_items = list(out_items)
                        refreshed._incremental_fast_hits = int(getattr(self, "_incremental_fast_hits", 0))  # type: ignore[attr-defined]
                    except Exception:
                        pass

            if refreshed is not None and inplace:
                self._items = list(refreshed.dump_records())
                self._ctx = ctx
                self._cache_valid = True
                try:
                    self._reload_cursor_ts = getattr(refreshed, "_reload_cursor_ts", None)
                    self._incremental_seed = getattr(refreshed, "_incremental_seed", None)
                    self._incremental_base_items = getattr(refreshed, "_incremental_base_items", None)
                    self._last_seen_bus_rev = getattr(refreshed, "_last_seen_bus_rev", None)
                    self._incremental_cached_items = getattr(refreshed, "_incremental_cached_items", None)
                    self._incremental_fast_hits = int(getattr(refreshed, "_incremental_fast_hits", getattr(self, "_incremental_fast_hits", 0)))
                except Exception:
                    pass
                return self

            if refreshed is not None:
                return refreshed

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

    async def reload_with_async(
        self,
        ctx: Optional[BusReplayContext] = None,
        *,
        inplace: bool = False,
        incremental: bool = False,
    ) -> "BusList[TRecord]":
        """异步版本的 reload_with，使用 asyncio.to_thread 包装同步调用。
        
        Note: 底层 ZMQ socket 是同步的，此方法通过线程池实现非阻塞。
        
        Args:
            ctx: 上下文，提供 ctx.bus.* 客户端
            inplace: True 时原对象会被更新，False 时返回新列表
            incremental: True 时使用增量重载
            
        Returns:
            刷新后的 BusList
        """
        import asyncio
        return await asyncio.to_thread(
            self.reload_with,
            ctx,
            inplace=inplace,
            incremental=incremental,
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

        if str(op) == "add":
            # Fast path only works when full record payload is included.
            # With lightweight deltas (no record), fall back to reload+diff.
            rec_raw = payload.get("record")
            if not isinstance(rec_raw, dict):
                return None
            rec = self._record_from_raw(rec_raw)
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

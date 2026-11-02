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
    from plugin.core.bus.frames import FrameList

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


class _FramesClientProto(Protocol):
    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> "FrameList": ...


class BusHubProtocol(Protocol):
    """Bus Hub protocol exposing the various bus clients.
    
    Attributes:
        messages: Message-query client.
        events: Event-query client.
        lifecycle: Lifecycle-event query client.
        memory: Memory-data query client.
        conversations: Conversation-context query client.
        frames: Recent host-supplied model frames, lossy rather than a log.
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

    @property
    def frames(self) -> _FramesClientProto: ...


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

    # Enable this for read-only snapshot-store lists. Lazy sort()/limit()/filter()
    # record plans rather than evaluating locally, replaying on materialization via synchronous client.get().
    # Inside event loops get forwards to get_async and returns a coroutine, making chaining after
    # await bus.frames.get(...) raise AttributeError and leak an unawaited coroutine.
    # Snapshots represent that specific read, with no replay-to-latest semantics, so local
    # evaluation is correct. Retain _plan because reload_with()/reload_with_async() consult
    # it directly rather than this flag, preserving explicit refresh.
    _snapshot_chain: bool = False

    def _is_lazy_mode(self) -> bool:
        if self._snapshot_chain:
            return False
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

        Convert each record to a dictionary, usually via record.dump(), for serialization/logging.
        English: Convert each record to a dict (typically via record.dump()) for serialization/logging.
        """
        self._ensure_materialized()
        return [x.dump() for x in self._items]

    def dump_records(self) -> List[TRecord]:
        """Return a shallow copy of the underlying record list.

        Return a shallow copy of the current record list with original record objects.
        English: Return a shallow copy of record objects.
        """
        self._ensure_materialized()
        return list(self._items)

    @property
    def trace(self) -> Tuple[BusOp, ...]:
        return self._trace

    def trace_dump(self) -> List[Dict[str, Any]]:
        """Dump the recorded query trace.

        Return serializable trace data for debugging/displaying chained operations.
        English: Return a serializable trace describing the chained operations.

        Note:
            - Traces support observability only, without replay guarantees.
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

        Return the replayable TraceNode plan tree for watchers/reload.
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

        Generate the current list's query-expression string for debugging/printing.
        English: Return a human-readable query expression for debugging.

        Note:
            - Prefer plan.explain() when available for accuracy.
            - Otherwise concatenate trace entries.
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

        Return a new BusList sorted by fields or a custom key.
        English: Return a new BusList sorted by fields or a custom key.

        Args:
            by:
                Fields to sort by, one name or a list; empty attempts default fields.
                (timestamp/created_at/time).
                English: Sort by field name(s). If omitted, tries default fields
                (timestamp/created_at/time).
            key:
                Custom sorting function, mutually exclusive with by.
                English: Custom sort key callable. Mutually exclusive with by.
            cast:
                Simple field-value coercion: \"int\"/\"float\"/\"str\".
                English: Optional value casting for field values: "int"/"float"/"str".
            reverse:
                Whether to reverse order.
                English: Sort descending when True.

        Note:
            - reload() cannot replay sort(key=callable).
            - reload()/watch() can replay sort(by=...).
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

        Filter records by BusFilter/keyword arguments, returning a new BusList.
        English: Filter records using BusFilter or keyword arguments, returning a new BusList.

        Args:
            flt:
                Prebuilt BusFilter; create from kwargs when absent.
                English: Optional BusFilter. When None, build from kwargs.
            strict:
                Strict mode raises for invalid regexes / failed type conversions.
                English: Strict mode. Raise on invalid regex / invalid numeric conversions.
            **kwargs:
                BusFilter field shorthand, e.g. source=..., type=..., priority_min=....
                English: Convenience fields for BusFilter (e.g. source/type/priority_min...).

        Returns:
            A new BusList.

        Note:
            - filter(...) is replayable through reload()/watch().
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

        Filter with any Python predicate, returning a new BusList.
        English: Filter with an arbitrary Python predicate callable.

        Warning:
            - where(predicate) cannot be serialized/replayed by reload()/watch().
            - Use filter(...) for reload()/watch(); this operation is neither serializable nor replayable.
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

        Return a new BusList containing the first n records.
        English: Return a new BusList containing at most the first n records.

        Note:
            - n<=0 returns an empty list.
            - limit(...) is replayable through reload()/watch().
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
                elif bus == "frames":
                    # Read-only stores still need replay support: BusRpcClientBase attaches bus-named GetNodes
                    # to every get(), so a missing branch does not merely omit functionality;
                    # documented frames.sort(...).limit(1) chaining instead raises
                    # NonReplayableTraceError. The same applies to conversations.
                    return _as_eager(ctx.bus.frames.get(**params))
                elif bus == "conversations":
                    return _as_eager(ctx.bus.conversations.get(**params))
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

        Replay the plan against the bus with the same chained operations, returning a current BusList.
        English: Reload from bus by replaying the stored plan and operations.

        Requirements:
            - Requires replayable plans, usually built from get()/filter()/sort(by=...)/limit().
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

        Underlying reload implementation, optionally updating this object directly with inplace=True.
        English: Underlying reload implementation; can mutate current instance when inplace=True.

        Args:
            ctx:
                Supply ctx.bus.messages/events/lifecycle and related interfaces.
                English: Context providing ctx.bus.* clients.
            inplace:
                True updates the same instance; False returns a new list.
                English: When True, mutate this instance; otherwise return a new list.

        Raises:
            NonReplayableTraceError: Missing plan or nonreplayable steps such as sort(key=callable)/where(predicate).
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
        """Async reload_with wraps synchronous calls in asyncio.to_thread.
        
        Note: underlying ZMQ sockets are synchronous; use the thread pool for nonblocking behavior.
        
        Args:
            ctx: Context exposing ctx.bus.* clients.
            inplace: True updates the original object; False returns a new list.
            
        Returns:
            Refreshed BusList.
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

        Create a watcher from the current replayable plan to observe bus changes and invoke subscribe callbacks.
        English: Create a watcher based on the replayable plan for change notifications.

        Args:
            ctx:
                Supply ctx.bus.* and required IPC capabilities within plugin processes.
                English: Context providing bus clients and (in plugin process) IPC capability.
            bus:
                Explicit bus type (\"messages\"/\"events\"/\"lifecycle\"), otherwise inferred from plan.
                English: Override bus name; otherwise inferred from the plan.
            debounce_ms:
                Debounce milliseconds; positive values combine nearby bus changes to reduce reloads.
                English: Debounce window in milliseconds. When >0, coalesce bursts of bus changes.

        Note:
            - Watchers require replayable plans; operations like where(predicate) raise.
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

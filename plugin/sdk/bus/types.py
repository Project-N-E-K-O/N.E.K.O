from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable, Dict, Generic, Iterator, List, Optional, Sequence, Tuple, TypeVar, Union


TRecord = TypeVar("TRecord", bound="BusRecord")


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
        trace: Optional[Sequence[BusOp]] = None,
        plan: Optional[TraceNode] = None,
        fast_mode: bool = False,
    ):
        self._items: List[TRecord] = list(items)
        self._fast_mode = bool(fast_mode)
        self._trace: Tuple[BusOp, ...] = tuple(trace or ()) if not self._fast_mode else ()
        self._plan: Optional[TraceNode] = plan if not self._fast_mode else None

    def __iter__(self) -> Iterator[TRecord]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def count(self) -> int:
        return len(self._items)

    def size(self) -> int:
        return len(self._items)

    def __getitem__(self, idx: int) -> TRecord:
        return self._items[idx]

    def dump(self) -> List[Dict[str, Any]]:
        return [x.dump() for x in self._items]

    def dump_records(self) -> List[TRecord]:
        return list(self._items)

    @property
    def fast_mode(self) -> bool:
        return self._fast_mode

    @property
    def trace(self) -> Tuple[BusOp, ...]:
        return self._trace

    def trace_dump(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": op.name,
                "params": dict(op.params) if isinstance(op.params, dict) else {},
                "at": op.at,
            }
            for op in self._trace
        ]

    def trace_tree_dump(self) -> Optional[Dict[str, Any]]:
        if self._plan is None:
            return None
        return self._plan.dump()

    def explain(self) -> str:
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
            "trace": trace,
            "plan": plan,
            "fast_mode": self._fast_mode,
        }
        if hasattr(self, "plugin_id"):
            kwargs["plugin_id"] = getattr(self, "plugin_id")
        try:
            return self.__class__(items, **kwargs)  # type: ignore[call-arg]
        except TypeError:
            kwargs.pop("plugin_id", None)
            return self.__class__(items, **kwargs)  # type: ignore[call-arg]

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

        trace = self._add_trace("merge", {"left": len(self), "right": len(other)})
        plan = self._add_plan_binary("merge", other, {"left": len(self), "right": len(other)})
        return self._construct(merged, trace, plan)

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
        if key is not None and by is not None:
            raise ValueError("Specify only one of 'key' or 'by'")

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
        return self._construct(items, trace, plan)

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

        trace = self._add_trace("intersection", {"left": len(self), "right": len(other)})
        plan = self._add_plan_binary("intersection", other, {"left": len(self), "right": len(other)})
        return self._construct(kept, trace, plan)

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

        trace = self._add_trace("difference", {"left": len(self), "right": len(other)})
        plan = self._add_plan_binary("difference", other, {"left": len(self), "right": len(other)})
        return self._construct(kept, trace, plan)

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

        items = [item for item in self._items if _match(item)]
        params: Dict[str, Any] = {}
        try:
            params.update({k: v for k, v in vars(flt).items() if v is not None})
        except Exception:
            params["flt"] = str(flt)
        params["strict"] = strict
        trace = self._add_trace("filter", params)
        plan = self._add_plan_unary("filter", params)
        return self._construct(items, trace, plan)

    def where_in(self, field: str, values: Sequence[Any]) -> "BusList[TRecord]":
        vs = list(values)

        items = [item for item in self._items if self._get_field(item, field) in vs]
        trace = self._add_trace("where_in", {"field": field, "values": vs})
        plan = self._add_plan_unary("where_in", {"field": field, "values": vs})
        return self._construct(items, trace, plan)

    def where_eq(self, field: str, value: Any) -> "BusList[TRecord]":
        items = [item for item in self._items if self._get_field(item, field) == value]
        trace = self._add_trace("where_eq", {"field": field, "value": value})
        plan = self._add_plan_unary("where_eq", {"field": field, "value": value})
        return self._construct(items, trace, plan)

    def where_contains(self, field: str, value: str) -> "BusList[TRecord]":
        needle = str(value)
        items: List[TRecord] = []
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
        return self._construct(items, trace, plan)

    def where_regex(self, field: str, pattern: str, *, strict: bool = True) -> "BusList[TRecord]":
        pat = str(pattern)
        try:
            compiled = re.compile(pat)
        except re.error as e:
            if strict:
                raise BusFilterError(f"Invalid regex for where_regex({field}): {pat!r}") from e
            compiled = None

        items: List[TRecord] = []
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
        return self._construct(items, trace, plan)

    def where_gt(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        items: List[TRecord] = []
        for item in self._items:
            v = self._cast_value(self._get_field(item, field), cast)
            try:
                if v > target:
                    items.append(item)
            except Exception:
                continue
        trace = self._add_trace("where_gt", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_gt", {"field": field, "value": value, "cast": cast})
        return self._construct(items, trace, plan)

    def where_ge(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        items: List[TRecord] = []
        for item in self._items:
            v = self._cast_value(self._get_field(item, field), cast)
            try:
                if v >= target:
                    items.append(item)
            except Exception:
                continue
        trace = self._add_trace("where_ge", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_ge", {"field": field, "value": value, "cast": cast})
        return self._construct(items, trace, plan)

    def where_lt(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        items: List[TRecord] = []
        for item in self._items:
            v = self._cast_value(self._get_field(item, field), cast)
            try:
                if v < target:
                    items.append(item)
            except Exception:
                continue
        trace = self._add_trace("where_lt", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_lt", {"field": field, "value": value, "cast": cast})
        return self._construct(items, trace, plan)

    def where_le(self, field: str, value: Any, *, cast: Optional[str] = None) -> "BusList[TRecord]":
        target = self._cast_value(value, cast)
        items: List[TRecord] = []
        for item in self._items:
            v = self._cast_value(self._get_field(item, field), cast)
            try:
                if v <= target:
                    items.append(item)
            except Exception:
                continue
        trace = self._add_trace("where_le", {"field": field, "value": value, "cast": cast})
        plan = self._add_plan_unary("where_le", {"field": field, "value": value, "cast": cast})
        return self._construct(items, trace, plan)

    def try_filter(self, flt: Optional[BusFilter] = None, **kwargs: Any) -> BusFilterResult[TRecord]:
        try:
            value = self.filter(flt, strict=True, **kwargs)
            return BusFilterResult(ok=True, value=value, error=None)
        except BusFilterError as e:
            return BusFilterResult(ok=False, value=None, error=e)

    def where(self, predicate: Callable[[TRecord], bool]) -> "BusList[TRecord]":
        items = [item for item in self._items if predicate(item)]
        trace = self._add_trace(
            "where",
            {"predicate": getattr(predicate, "__name__", "<callable>")},
        )
        # Not replayable: predicate is arbitrary callable.
        plan = self._add_plan_unary("where", {"predicate": getattr(predicate, "__name__", "<callable>")})
        return self._construct(items, trace, plan)

    def limit(self, n: int) -> "BusList[TRecord]":
        nn = int(n)
        if nn <= 0:
            trace = self._add_trace("limit", {"n": nn})
            plan = self._add_plan_unary("limit", {"n": nn})
            return self._construct([], trace, plan)
        trace = self._add_trace("limit", {"n": nn})
        plan = self._add_plan_unary("limit", {"n": nn})
        return self._construct(self._items[:nn], trace, plan)

    def _replay_plan(self, ctx: Any, plan: TraceNode) -> "BusList[TRecord]":
        if isinstance(plan, GetNode):
            bus = str(plan.params.get("bus") or "").strip()
            params = dict(plan.params.get("params") or {})
            if bus == "messages":
                return ctx.bus.messages.get(**params)
            if bus == "events":
                return ctx.bus.events.get(**params)
            if bus == "lifecycle":
                return ctx.bus.lifecycle.get(**params)
            raise NonReplayableTraceError(f"Unknown bus for reload: {bus!r}")

        if isinstance(plan, UnaryNode):
            base = self._replay_plan(ctx, plan.child)
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
            left = self._replay_plan(ctx, plan.left)
            right = self._replay_plan(ctx, plan.right)
            if plan.op == "merge":
                return left + right
            if plan.op == "intersection":
                return left & right
            if plan.op == "difference":
                return left - right
            raise NonReplayableTraceError(f"Unknown binary op for reload: {plan.op!r}")

        raise NonReplayableTraceError(f"Unknown plan node type: {type(plan).__name__}")

    def reload(self, ctx: Any) -> "BusList[TRecord]":
        return self.reload_with(ctx)

    def reload_with(self, ctx: Any, *, inplace: bool = False) -> "BusList[TRecord]":
        if self._plan is None:
            raise NonReplayableTraceError("reload is unavailable when fast_mode=True or plan is missing")

        refreshed = self._replay_plan(ctx, self._plan)
        if not inplace:
            return refreshed

        # In-place refresh: mutate current instance to hold latest items, keep same plan.
        self._items = list(refreshed.dump_records())
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

"""Bus contract models for SDK v2 shared bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue

TBusRecord = TypeVar("TBusRecord")


class BusReplayContext(Protocol):
    pass


@dataclass(slots=True)
class BusConversation:
    id: str
    topic: str
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> "BusConversation":
        if not isinstance(raw, dict):
            return cls(id="", topic="")
        return cls(id=str(raw.get("id", "")), topic=str(raw.get("topic", "")), metadata=dict(raw.get("metadata", {}) or {}))

    @classmethod
    def from_index(cls, index: JsonObject, payload: JsonObject | None = None) -> "BusConversation":
        data = dict(payload or {})
        data.setdefault("id", index.get("id", ""))
        data.setdefault("topic", index.get("topic", ""))
        return cls.from_raw(data)

    def dump(self) -> JsonObject:
        return {"id": self.id, "topic": self.topic, "metadata": dict(self.metadata)}

    def key(self) -> str:
        return self.id

    def version(self) -> int | None:
        return None


@dataclass(slots=True)
class BusMessage:
    id: str
    conversation_id: str
    role: str
    content: JsonValue
    timestamp: float | None = None
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> "BusMessage":
        if not isinstance(raw, dict):
            return cls(id="", conversation_id="", role="", content=None)
        return cls(
            id=str(raw.get("id", "")),
            conversation_id=str(raw.get("conversation_id", "")),
            role=str(raw.get("role", "")),
            content=raw.get("content"),
            timestamp=float(raw["timestamp"]) if raw.get("timestamp") is not None else None,
            metadata=dict(raw.get("metadata", {}) or {}),
        )

    @classmethod
    def from_index(cls, index: JsonObject, payload: JsonObject | None = None) -> "BusMessage":
        data = dict(payload or {})
        data.setdefault("id", index.get("id", ""))
        data.setdefault("conversation_id", index.get("conversation_id", ""))
        data.setdefault("role", index.get("role", ""))
        return cls.from_raw(data)

    def dump(self) -> JsonObject:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    def key(self) -> str:
        return self.id

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class BusEvent:
    id: str
    event_type: str
    payload: JsonObject = field(default_factory=dict)
    timestamp: float | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "BusEvent":
        if not isinstance(raw, dict):
            return cls(id="", event_type="")
        return cls(
            id=str(raw.get("id", "")),
            event_type=str(raw.get("event_type", raw.get("type", ""))),
            payload=dict(raw.get("payload", {}) or {}),
            timestamp=float(raw["timestamp"]) if raw.get("timestamp") is not None else None,
        )

    @classmethod
    def from_index(cls, index: JsonObject, payload: JsonObject | None = None) -> "BusEvent":
        data = dict(payload or {})
        data.setdefault("id", index.get("id", ""))
        data.setdefault("event_type", index.get("event_type", index.get("type", "")))
        return cls.from_raw(data)

    def dump(self) -> JsonObject:
        return {"id": self.id, "event_type": self.event_type, "payload": dict(self.payload), "timestamp": self.timestamp}

    def key(self) -> str:
        return self.id

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class BusRecord:
    id: str
    namespace: str
    payload: JsonObject = field(default_factory=dict)
    rev: int = 0

    @classmethod
    def from_raw(cls, raw: object) -> "BusRecord":
        if not isinstance(raw, dict):
            return cls(id="", namespace="")
        return cls(
            id=str(raw.get("id", "")),
            namespace=str(raw.get("namespace", "")),
            payload=dict(raw.get("payload", {}) or {}),
            rev=int(raw.get("rev", 0) or 0),
        )

    @classmethod
    def from_index(cls, index: JsonObject, payload: JsonObject | None = None) -> "BusRecord":
        data = dict(payload or {})
        data.setdefault("id", index.get("id", ""))
        data.setdefault("namespace", index.get("namespace", ""))
        data.setdefault("rev", index.get("rev", 0))
        return cls.from_raw(data)

    def dump(self) -> JsonObject:
        return {"id": self.id, "namespace": self.namespace, "payload": dict(self.payload), "rev": self.rev}

    def key(self) -> str:
        return f"{self.namespace}:{self.id}"

    def version(self) -> int | None:
        return self.rev


class BusItemProtocol(Protocol):
    def key(self) -> str: ...
    def version(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class BusChange:
    namespace: str
    record_id: str
    rev: int


@dataclass(frozen=True)
class BusFilter:
    namespace: str | None = None
    record_id: str | None = None


class BusFilterError(ValueError):
    pass


class NonReplayableTraceError(RuntimeError):
    pass


@dataclass(frozen=True)
class BusFilterResult(Generic[TBusRecord]):
    ok: bool
    value: "BusList[TBusRecord] | None" = None
    error: Exception | None = None


@dataclass(frozen=True)
class BusOp:
    name: str
    params: JsonObject
    at: float


@dataclass(frozen=True)
class TraceNode:
    op: str
    params: JsonObject
    at: float

    def dump(self) -> JsonObject:
        return {"op": self.op, "params": dict(self.params), "at": self.at}

    def explain(self) -> str:
        return f"{self.op}({dict(self.params)})" if self.params else f"{self.op}()"


@dataclass(frozen=True)
class GetNode(TraceNode):
    def dump(self) -> JsonObject:
        base = super().dump()
        base["kind"] = "get"
        return base


@dataclass(frozen=True)
class UnaryNode(TraceNode):
    child: TraceNode

    def dump(self) -> JsonObject:
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

    def dump(self) -> JsonObject:
        base = super().dump()
        base["kind"] = "binary"
        base["left"] = self.left.dump()
        base["right"] = self.right.dump()
        return base

    def explain(self) -> str:
        return f"({self.left.explain()}) {self.op} ({self.right.explain()})"


@dataclass(slots=True)
class BusList(Generic[TBusRecord]):
    items: list[TBusRecord]
    trace: list[BusOp] = field(default_factory=list)
    fast_mode: bool = False
    _reload_factory: object | None = None

    @property
    def fast_mode_enabled(self) -> bool:
        return self.fast_mode

    def __iter__(self):
        return iter(self.items)

    def count(self) -> int:
        return len(self.items)

    def size(self) -> int:
        return len(self.items)

    def dump(self) -> list[JsonObject]:
        dumped: list[JsonObject] = []
        for item in self.items:
            dumper = getattr(item, "dump", None)
            dumped.append(dumper() if callable(dumper) else {"value": str(item)})
        return dumped

    def dump_records(self) -> list[JsonObject]:
        return self.dump()

    def explain(self) -> str:
        if not self.trace:
            return f"BusList(count={len(self.items)})"
        return " -> ".join(op.name for op in self.trace)

    def trace_dump(self) -> list[JsonObject]:
        return [{"name": op.name, "params": dict(op.params), "at": op.at} for op in self.trace]

    def trace_tree_dump(self) -> JsonObject | None:
        if not self.trace:
            return None
        root: JsonObject = {"kind": "trace", "ops": self.trace_dump()}
        return root

    def merge(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        return BusList(items=[*self.items, *other.items], trace=[*self.trace, *other.trace], fast_mode=self.fast_mode and other.fast_mode, _reload_factory=self._reload_factory)

    def sorted(self, key=None, reverse: bool = False) -> "BusList[TBusRecord]":
        return BusList(items=sorted(self.items, key=key, reverse=reverse), trace=list(self.trace), fast_mode=self.fast_mode, _reload_factory=self._reload_factory)

    def sort(self, key=None, reverse: bool = False) -> "BusList[TBusRecord]":
        self.items.sort(key=key, reverse=reverse)
        return self

    def filter(self, predicate) -> "BusList[TBusRecord]":
        return BusList(items=[item for item in self.items if predicate(item)], trace=list(self.trace), fast_mode=self.fast_mode, _reload_factory=self._reload_factory)

    def where(self, predicate) -> "BusList[TBusRecord]":
        return self.filter(predicate)

    def where_in(self, field: str, values) -> "BusList[TBusRecord]":
        value_set = set(values)
        return self.filter(lambda item: getattr(item, field, None) in value_set)

    def where_eq(self, field: str, value) -> "BusList[TBusRecord]":
        return self.filter(lambda item: getattr(item, field, None) == value)

    def where_contains(self, field: str, value: str) -> "BusList[TBusRecord]":
        return self.filter(lambda item: value in str(getattr(item, field, "")))

    def where_regex(self, field: str, pattern: str, *, strict: bool = True) -> "BusList[TBusRecord]":
        import re
        regex = re.compile(pattern)
        return self.filter(lambda item: bool(regex.search(str(getattr(item, field, "")))))

    def _coerce_cmp_value(self, item, field: str, cast: str | None = None):
        value = getattr(item, field, None)
        if cast == "timestamp":
            from .records import parse_iso_timestamp
            return parse_iso_timestamp(value)
        return value

    def where_gt(self, field: str, value, *, cast: str | None = None) -> "BusList[TBusRecord]":
        return self.filter(lambda item: (self._coerce_cmp_value(item, field, cast) is not None) and self._coerce_cmp_value(item, field, cast) > value)

    def where_ge(self, field: str, value, *, cast: str | None = None) -> "BusList[TBusRecord]":
        return self.filter(lambda item: (self._coerce_cmp_value(item, field, cast) is not None) and self._coerce_cmp_value(item, field, cast) >= value)

    def where_lt(self, field: str, value, *, cast: str | None = None) -> "BusList[TBusRecord]":
        return self.filter(lambda item: (self._coerce_cmp_value(item, field, cast) is not None) and self._coerce_cmp_value(item, field, cast) < value)

    def where_le(self, field: str, value, *, cast: str | None = None) -> "BusList[TBusRecord]":
        return self.filter(lambda item: (self._coerce_cmp_value(item, field, cast) is not None) and self._coerce_cmp_value(item, field, cast) <= value)

    def intersection(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        other_dump = {str(item) for item in other.items}
        return self.filter(lambda item: str(item) in other_dump)

    def intersect(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        return self.intersection(other)

    def difference(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        other_dump = {str(item) for item in other.items}
        return self.filter(lambda item: str(item) not in other_dump)

    def subtract(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        return self.difference(other)

    def try_filter(self, predicate):
        try:
            return BusFilterResult(ok=True, value=self.filter(predicate))
        except Exception as error:
            return BusFilterResult(ok=False, error=error)

    def limit(self, size: int) -> "BusList[TBusRecord]":
        return BusList(items=list(self.items[:size]), trace=list(self.trace), fast_mode=self.fast_mode, _reload_factory=self._reload_factory)

    def query(self) -> "BusQuery[TBusRecord]":
        return BusQuery(self)

    def reload(self, ctx=None) -> "BusList[TBusRecord]":
        return self.query().reload(ctx)

    async def reload_async(self, ctx=None) -> "BusList[TBusRecord]":
        return await self.query().reload_async(ctx)

    def reload_with(self, factory) -> "BusList[TBusRecord]":
        return self.query().reload_with(factory)

    async def reload_with_async(self, factory) -> "BusList[TBusRecord]":
        return await self.query().reload_with_async(factory)

    def watch(self, bus_client, channel: str = "*"):
        return self.query().watch(bus_client, channel)

    async def watch_async(self, bus_client, channel: str = "*"):
        return await self.query().watch_async(bus_client, channel)


@dataclass(frozen=True)
class BusQueryPlan:
    trace: tuple[BusOp, ...] = ()

    def dump(self) -> JsonObject:
        return {"kind": "query_plan", "ops": [{"name": op.name, "params": dict(op.params), "at": op.at} for op in self.trace]}

    def explain(self) -> str:
        if not self.trace:
            return "query_plan()"
        return " -> ".join(op.name for op in self.trace)


class BusWatcher:
    def __init__(self, watcher):
        self._watcher = watcher

    def __getattr__(self, name: str):
        return getattr(self._watcher, name)


class BusQuery(Generic[TBusRecord]):
    def __init__(self, source: BusList[TBusRecord]):
        self._source = source
        self._plan = BusQueryPlan(tuple(source.trace))

    @property
    def plan(self) -> BusQueryPlan:
        return self._plan

    def reload(self, ctx=None) -> BusList[TBusRecord]:
        factory = self._source._reload_factory
        if callable(factory):
            result = factory(ctx)
            if isinstance(result, BusList):
                return result
        return BusList(items=list(self._source.items), trace=list(self._source.trace), fast_mode=self._source.fast_mode, _reload_factory=self._source._reload_factory)

    async def reload_async(self, ctx=None) -> BusList[TBusRecord]:
        return self.reload(ctx)

    def reload_with(self, factory) -> BusList[TBusRecord]:
        return BusList(items=list(self._source.items), trace=list(self._source.trace), fast_mode=self._source.fast_mode, _reload_factory=factory)

    async def reload_with_async(self, factory) -> BusList[TBusRecord]:
        return self.reload_with(factory)

    def watch(self, bus_client, channel: str = "*") -> BusWatcher:
        from .watchers import BusListWatcher
        return BusWatcher(BusListWatcher("watch:list", bus_client, channel))

    async def watch_async(self, bus_client, channel: str = "*") -> BusWatcher:
        return self.watch(bus_client, channel)


__all__ = [
    "BusConversation",
    "BusMessage",
    "BusEvent",
    "BusRecord",
    "BusItemProtocol",
    "BusChange",
    "BusFilter",
    "BusFilterError",
    "NonReplayableTraceError",
    "BusFilterResult",
    "BusOp",
    "TraceNode",
    "GetNode",
    "UnaryNode",
    "BinaryNode",
    "BusReplayContext",
    "BusList",
    "BusQueryPlan",
    "BusQuery",
    "BusWatcher",
]

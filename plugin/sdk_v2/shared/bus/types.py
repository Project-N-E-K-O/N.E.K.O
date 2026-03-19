"""Bus contract models for SDK v2 shared bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar, cast

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue

TBusRecord = TypeVar("TBusRecord")


def _as_object_dict(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    try:
        return cast(JsonObject, dict(value))
    except (TypeError, ValueError):
        return {}


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast(str | int | float, value))
    except (TypeError, ValueError):
        return None


def _as_int(value: object, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(cast(str | int | float, value))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class BusConversation:
    id: str
    topic: str
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> "BusConversation":
        if not isinstance(raw, dict):
            return cls(id="", topic="")
        return cls(
            id=str(raw.get("id", "")),
            topic=str(raw.get("topic", "")),
            metadata=_as_object_dict(raw.get("metadata")),
        )

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
            timestamp=_as_optional_float(raw.get("timestamp")),
            metadata=_as_object_dict(raw.get("metadata")),
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

    def version(self) -> float | None:
        return self.timestamp


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
            payload=_as_object_dict(raw.get("payload")),
            timestamp=_as_optional_float(raw.get("timestamp")),
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

    def version(self) -> float | None:
        return self.timestamp


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
            payload=_as_object_dict(raw.get("payload")),
            rev=_as_int(raw.get("rev"), default=0),
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

    def version(self) -> int:
        return self.rev


class BusItemProtocol(Protocol):
    def key(self) -> str: ...
    def version(self) -> float | int | None: ...


@dataclass(frozen=True, slots=True)
class BusChange:
    namespace: str
    record_id: str
    rev: int


@dataclass(frozen=True)
class BusFilter:
    namespace: str | None = None
    record_id: str | None = None


@dataclass(slots=True)
class BusList(Generic[TBusRecord]):
    """Minimal typed list wrapper for bus query results."""

    items: list[TBusRecord]

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def count(self) -> int:
        return len(self.items)

    def dump(self) -> list[JsonObject]:
        dumped: list[JsonObject] = []
        for item in self.items:
            dumper = getattr(item, "dump", None)
            dumped_item = dumper() if callable(dumper) else {"value": str(item)}
            dumped.append(cast(JsonObject, dumped_item if isinstance(dumped_item, dict) else {"value": str(dumped_item)}))
        return dumped

    def dump_records(self) -> list[JsonObject]:
        return self.dump()

    def filter(self, predicate: Callable[[TBusRecord], bool]) -> "BusList[TBusRecord]":
        return BusList(items=[item for item in self.items if predicate(item)])

    def where(self, predicate: Callable[[TBusRecord], bool]) -> "BusList[TBusRecord]":
        return self.filter(predicate)

    def sorted(self, key: Callable[[TBusRecord], Any] | None = None, reverse: bool = False) -> "BusList[TBusRecord]":
        if key is None:
            return BusList(items=sorted(self.items, reverse=reverse))  # type: ignore[type-var]
        return BusList(items=sorted(self.items, key=key, reverse=reverse))  # type: ignore[type-var]

    def limit(self, size: int) -> "BusList[TBusRecord]":
        return BusList(items=list(self.items[:size]))

    def merge(self, other: "BusList[TBusRecord]") -> "BusList[TBusRecord]":
        return BusList(items=[*self.items, *other.items])


__all__ = [
    "BusChange",
    "BusConversation",
    "BusEvent",
    "BusFilter",
    "BusItemProtocol",
    "BusList",
    "BusMessage",
    "BusRecord",
]

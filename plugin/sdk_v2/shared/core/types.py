"""Shared core contract types for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, MutableMapping, Protocol, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
Metadata: TypeAlias = Mapping[str, JsonValue]
InputSchema: TypeAlias = Mapping[str, JsonValue]
EntryHandler: TypeAlias = Callable[..., object]


class LoggerLike(Protocol):
    """Minimal logger contract used by SDK interfaces."""

    def debug(self, message: str, *args: object, **kwargs: object) -> object: ...

    def info(self, message: str, *args: object, **kwargs: object) -> object: ...

    def warning(self, message: str, *args: object, **kwargs: object) -> object: ...

    def error(self, message: str, *args: object, **kwargs: object) -> object: ...

    def exception(self, message: str, *args: object, **kwargs: object) -> object: ...


@dataclass(slots=True)
class PluginRef:
    plugin_id: str


@dataclass(slots=True)
class EntryRef:
    plugin_id: str
    entry_id: str


@dataclass(slots=True)
class EventRef:
    plugin_id: str
    event_type: str
    event_id: str


class PluginContextProtocol(Protocol):
    plugin_id: str
    metadata: Metadata
    logger: LoggerLike | None


class MutableStateProtocol(Protocol):
    def as_dict(self) -> MutableMapping[str, JsonValue]:
        """Return mutable snapshot for persistence and freeze pipeline."""


class RouterProtocol(Protocol):
    def name(self) -> str:
        """Return router display name."""

    def set_prefix(self, prefix: str) -> None:
        """Set router entry id prefix."""

    def iter_handlers(self) -> Mapping[str, EntryHandler]:
        """Return `<entry_id, handler>` mapping."""


__all__ = [
    "JsonScalar",
    "JsonValue",
    "JsonObject",
    "Metadata",
    "InputSchema",
    "EntryHandler",
    "LoggerLike",
    "PluginRef",
    "EntryRef",
    "EventRef",
    "PluginContextProtocol",
    "MutableStateProtocol",
    "RouterProtocol",
]

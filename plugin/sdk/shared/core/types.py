"""Shared core contract types for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Protocol, TypeAlias


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


# ---------------------------------------------------------------------------
# Bus protocols (single set, use Optional at usage sites where needed)
# ---------------------------------------------------------------------------

class BusMessagesProtocol(Protocol):
    def get(self, **kwargs: object) -> object: ...


class BusEventsProtocol(Protocol):
    def get(self, **kwargs: object) -> object: ...


class BusLifecycleProtocol(Protocol):
    def get(self, **kwargs: object) -> object: ...


class BusConversationsProtocol(Protocol):
    def get(self, **kwargs: object) -> object: ...

    def get_by_id(self, conversation_id: str, max_count: int = 10, timeout: float | None = None) -> object: ...


class BusMemoryProtocol(Protocol):
    def get(self, *, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> object: ...


class BusProtocol(Protocol):
    messages: BusMessagesProtocol | None
    events: BusEventsProtocol | None
    lifecycle: BusLifecycleProtocol | None
    conversations: BusConversationsProtocol | None
    memory: BusMemoryProtocol | None


class PluginContextProtocol(Protocol):
    plugin_id: str
    metadata: Metadata
    logger: LoggerLike | None
    config_path: str | Path | None
    bus: BusProtocol | None

    async def get_own_config(self, timeout: float = 5.0) -> object: ...

    async def get_own_base_config(self, timeout: float = 5.0) -> object: ...

    async def get_own_profiles_state(self, timeout: float = 5.0) -> object: ...

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0) -> object: ...

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0) -> object: ...

    async def upsert_own_profile_config(
        self,
        profile_name: str,
        config: JsonObject,
        *,
        make_active: bool = False,
        timeout: float = 10.0,
    ) -> object: ...

    async def delete_own_profile_config(self, profile_name: str, timeout: float = 10.0) -> object: ...

    async def set_own_active_profile(self, profile_name: str, timeout: float = 10.0) -> object: ...

    async def query_plugins(self, filters: dict[str, object], timeout: float = 5.0) -> object: ...

    async def trigger_plugin_event(self, **kwargs: object) -> object: ...

    async def get_system_config(self, timeout: float = 5.0) -> object: ...

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0) -> object: ...

    async def run_update(
        self,
        *,
        run_id: str | None = None,
        progress: float | None = None,
        stage: str | None = None,
        message: str | None = None,
        step: int | None = None,
        step_total: int | None = None,
        eta_seconds: float | None = None,
        metrics: dict[str, object] | None = None,
        timeout: float = 5.0,
    ) -> object: ...

    async def export_push(
        self,
        *,
        export_type: str,
        run_id: str | None = None,
        text: str | None = None,
        json_data: dict[str, object] | None = None,
        url: str | None = None,
        binary_data: bytes | None = None,
        binary_url: str | None = None,
        mime: str | None = None,
        description: str | None = None,
        label: str | None = None,
        metadata: dict[str, object] | None = None,
        delivery: str | bool | None = None,
        reply: bool | None = None,
        timeout: float = 5.0,
    ) -> object: ...

    async def finish(
        self,
        *,
        data: object = None,
        delivery: str | bool | None = None,
        reply: bool | None = None,
        message: str = "",
        trace_id: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> Any: ...

    def push_message(
        self,
        *,
        source: str,
        message_type: str,
        description: str = "",
        priority: int = 0,
        content: str | None = None,
        binary_data: bytes | None = None,
        binary_url: str | None = None,
        metadata: dict[str, object] | None = None,
        unsafe: bool = False,
        fast_mode: bool = False,
        target_lanlan: str | None = None,
        delivery: str | bool | None = None,
        reply: bool | None = None,
    ) -> object: ...

    def update_status(self, status: dict[str, object]) -> None: ...


class MutableStateProtocol(Protocol):
    def as_dict(self) -> MutableMapping[str, JsonValue]: ...


class RouterProtocol(Protocol):
    def name(self) -> str: ...

    def set_prefix(self, prefix: str) -> None: ...

    def iter_handlers(self) -> Mapping[str, EntryHandler]: ...


__all__ = [
    "BusConversationsProtocol",
    "BusEventsProtocol",
    "BusLifecycleProtocol",
    "BusMemoryProtocol",
    "BusMessagesProtocol",
    "BusProtocol",
    "EntryHandler",
    "EntryRef",
    "EventRef",
    "InputSchema",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "LoggerLike",
    "Metadata",
    "MutableStateProtocol",
    "PluginContextProtocol",
    "PluginRef",
    "RouterProtocol",
]

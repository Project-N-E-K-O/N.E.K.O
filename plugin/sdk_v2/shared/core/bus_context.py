"""SDK v2-owned host bus facade.

This module wraps the host-provided `ctx.bus` object so plugins interact with a
v2-owned surface instead of legacy SDK bus clients and records.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Mapping, Protocol, TypeVar, cast

from plugin.sdk_v2.shared.models import Err, Ok


def _mapping_get(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _read_first(value: object, *names: str) -> object:
    for name in names:
        found = _mapping_get(value, name)
        if found is not None:
            return found
    return None


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except Exception:
        return None


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except Exception:
        return default


def _iter_raw_items(raw_list: object) -> list[object]:
    if raw_list is None:
        return []
    dump_records = getattr(raw_list, "dump_records", None)
    if callable(dump_records):
        dumped = dump_records()
        if isinstance(dumped, list):
            return list(dumped)
    if isinstance(raw_list, Iterable) and not isinstance(raw_list, (str, bytes, bytearray, Mapping)):
        return list(raw_list)
    return []


@dataclass(slots=True)
class SdkBusMessageRecord:
    type: str
    timestamp: float | None = None
    plugin_id: str | None = None
    source: str | None = None
    priority: int = 0
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str | None = None
    message_type: str | None = None
    description: str | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "SdkBusMessageRecord":
        message_type = _as_str(_read_first(raw, "message_type", "type"))
        return cls(
            type=message_type or "MESSAGE",
            timestamp=_as_float(_read_first(raw, "timestamp", "time")),
            plugin_id=_as_str(_read_first(raw, "plugin_id")),
            source=_as_str(_read_first(raw, "source")),
            priority=_as_int(_read_first(raw, "priority")),
            content=_as_str(_read_first(raw, "content")),
            metadata=_as_dict(_read_first(raw, "metadata")),
            message_id=_as_str(_read_first(raw, "message_id", "id")),
            message_type=message_type,
            description=_as_str(_read_first(raw, "description")),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata),
            "message_id": self.message_id,
            "message_type": self.message_type,
            "description": self.description,
        }

    def key(self) -> str:
        return self.message_id or f"{self.source or ''}:{self.timestamp or 0}"

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class SdkBusEventRecord:
    type: str
    timestamp: float | None = None
    plugin_id: str | None = None
    source: str | None = None
    priority: int = 0
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str | None = None
    entry_id: str | None = None
    args: dict[str, Any] | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "SdkBusEventRecord":
        args_raw = _read_first(raw, "args")
        return cls(
            type=_as_str(_read_first(raw, "type", "event_type")) or "EVENT",
            timestamp=_as_float(_read_first(raw, "timestamp", "received_at", "time")),
            plugin_id=_as_str(_read_first(raw, "plugin_id")),
            source=_as_str(_read_first(raw, "source")),
            priority=_as_int(_read_first(raw, "priority")),
            content=_as_str(_read_first(raw, "content")),
            metadata=_as_dict(_read_first(raw, "metadata")),
            event_id=_as_str(_read_first(raw, "event_id", "trace_id", "id")),
            entry_id=_as_str(_read_first(raw, "entry_id")),
            args=cast(dict[str, Any] | None, dict(args_raw) if isinstance(args_raw, Mapping) else None),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata),
            "event_id": self.event_id,
            "entry_id": self.entry_id,
            "args": dict(self.args) if isinstance(self.args, dict) else self.args,
        }

    def key(self) -> str:
        return self.event_id or f"{self.type}:{self.timestamp or 0}"

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class SdkBusLifecycleRecord:
    type: str
    timestamp: float | None = None
    plugin_id: str | None = None
    source: str | None = None
    priority: int = 0
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    lifecycle_id: str | None = None
    detail: dict[str, Any] | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "SdkBusLifecycleRecord":
        detail = _read_first(raw, "detail")
        return cls(
            type=_as_str(_read_first(raw, "type")) or "lifecycle",
            timestamp=_as_float(_read_first(raw, "timestamp", "time", "at")),
            plugin_id=_as_str(_read_first(raw, "plugin_id")),
            source=_as_str(_read_first(raw, "source")),
            priority=_as_int(_read_first(raw, "priority")),
            content=_as_str(_read_first(raw, "content")),
            metadata=_as_dict(_read_first(raw, "metadata")),
            lifecycle_id=_as_str(_read_first(raw, "lifecycle_id", "trace_id", "id")),
            detail=_as_dict(detail) if detail is not None else None,
        )

    def dump(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata),
            "lifecycle_id": self.lifecycle_id,
            "detail": dict(self.detail) if isinstance(self.detail, dict) else self.detail,
        }

    def key(self) -> str:
        return self.lifecycle_id or f"{self.type}:{self.timestamp or 0}"

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class SdkBusConversationRecord:
    conversation_id: str | None = None
    type: str = "conversation"
    timestamp: float | None = None
    plugin_id: str | None = None
    source: str | None = None
    priority: int = 0
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    turn_type: str | None = None
    lanlan_name: str | None = None
    message_count: int | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "SdkBusConversationRecord":
        metadata = _as_dict(_read_first(raw, "metadata"))
        return cls(
            conversation_id=_as_str(_read_first(raw, "conversation_id", "id", "conversationId")),
            type=_as_str(_read_first(raw, "type", "message_type")) or "conversation",
            timestamp=_as_float(_read_first(raw, "timestamp", "time")),
            plugin_id=_as_str(_read_first(raw, "plugin_id")),
            source=_as_str(_read_first(raw, "source")),
            priority=_as_int(_read_first(raw, "priority")),
            content=_as_str(_read_first(raw, "content")),
            metadata=metadata,
            turn_type=_as_str(_read_first(raw, "turn_type")) or _as_str(metadata.get("turn_type")),
            lanlan_name=_as_str(_read_first(raw, "lanlan_name")) or _as_str(metadata.get("lanlan_name")),
            message_count=_as_int(_read_first(raw, "message_count", "count", "messageCount"), default=0),
        )

    def dump(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "type": self.type,
            "timestamp": self.timestamp,
            "plugin_id": self.plugin_id,
            "source": self.source,
            "priority": self.priority,
            "content": self.content,
            "metadata": dict(self.metadata),
            "turn_type": self.turn_type,
            "lanlan_name": self.lanlan_name,
            "message_count": self.message_count,
        }

    def key(self) -> str:
        return self.conversation_id or f"{self.lanlan_name or ''}:{self.timestamp or 0}"

    def version(self) -> int | None:
        return int(self.timestamp) if self.timestamp is not None else None


@dataclass(slots=True)
class SdkBusMemoryRecord:
    payload: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: object) -> "SdkBusMemoryRecord":
        if isinstance(raw, Mapping):
            return cls(payload={str(key): value for key, value in raw.items()})
        return cls(payload={"value": raw})

    def dump(self) -> dict[str, Any]:
        return dict(self.payload)

    def key(self) -> str:
        return str(self.payload.get("id", self.payload.get("key", repr(self.payload))))

    def version(self) -> int | None:
        rev = self.payload.get("rev")
        if rev is None:
            return None
        return _as_int(rev)


TRecord = TypeVar("TRecord")


class _RecordFactory(Protocol[TRecord]):
    @classmethod
    def from_raw(cls, raw: object) -> TRecord: ...


@dataclass(slots=True)
class SdkBusDelta(Generic[TRecord]):
    kind: str
    added: tuple[TRecord, ...]
    removed: tuple[str, ...]
    changed: tuple[TRecord, ...]
    current: "SdkBusList[TRecord]"


class SdkBusWatcher(Generic[TRecord]):
    def __init__(
        self,
        raw_watcher: object,
        *,
        namespace: str,
        record_factory: type[_RecordFactory[TRecord]],
        host_ctx: object,
    ) -> None:
        self._raw_watcher = raw_watcher
        self._namespace = namespace
        self._record_factory = record_factory
        self._host_ctx = host_ctx
        self._raw_watcher_task: asyncio.Task[object] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        if inspect.isawaitable(raw_watcher):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._raw_watcher = asyncio.run(raw_watcher)
            else:
                self._raw_watcher = None
                self._raw_watcher_task = loop.create_task(raw_watcher)

    async def _await_raw_watcher(self) -> object | None:
        if self._raw_watcher is not None:
            return self._raw_watcher
        task = self._raw_watcher_task
        if task is None:
            return None
        self._raw_watcher = await task
        self._raw_watcher_task = None
        return self._raw_watcher

    def _run_async_call(self, awaitable: object) -> None:
        if not inspect.isawaitable(awaitable):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(awaitable)
            return
        task = loop.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _invoke_raw_method(self, name: str) -> None:
        raw_watcher = self._raw_watcher
        if raw_watcher is not None:
            method = getattr(raw_watcher, name, None)
            if callable(method):
                self._run_async_call(method())
            return

        async def _call_later() -> None:
            resolved = await self._await_raw_watcher()
            method = getattr(resolved, name, None)
            if callable(method):
                result = method()
                if inspect.isawaitable(result):
                    await result

        self._run_async_call(_call_later())

    def start(self) -> "SdkBusWatcher[TRecord]":
        self._invoke_raw_method("start")
        return self

    def stop(self) -> None:
        self._invoke_raw_method("stop")

    def _wrap_delta(self, delta: object) -> SdkBusDelta[TRecord]:
        current = SdkBusList.from_raw(
            getattr(delta, "current", None),
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )
        added = tuple(
            self._record_factory.from_raw(item)
            for item in list(getattr(delta, "added", ()) or ())
        )
        changed = tuple(
            self._record_factory.from_raw(item)
            for item in list(getattr(delta, "changed", ()) or ())
        )
        removed = tuple(str(item) for item in list(getattr(delta, "removed", ()) or ()))
        return SdkBusDelta(
            kind=str(getattr(delta, "kind", "")),
            added=added,
            removed=removed,
            changed=changed,
            current=current,
        )

    def subscribe(self, *, on: str) -> Callable[[Callable[[SdkBusDelta[TRecord]], Any]], Callable[[SdkBusDelta[TRecord]], Any]]:
        def _decorator(fn: Callable[[SdkBusDelta[TRecord]], Any]) -> Callable[[SdkBusDelta[TRecord]], Any]:
            def _wrapped(delta: object) -> Any:
                return fn(self._wrap_delta(delta))

            raw_watcher = self._raw_watcher
            subscribe = getattr(raw_watcher, "subscribe", None)
            if raw_watcher is not None:
                if callable(subscribe):
                    raw_decorator = subscribe(on=on)
                    if inspect.isawaitable(raw_decorator):
                        async def _subscribe_async() -> None:
                            resolved_decorator = await raw_decorator
                            apply_result = resolved_decorator(_wrapped)
                            if inspect.isawaitable(apply_result):
                                await apply_result

                        self._run_async_call(_subscribe_async())
                    else:
                        apply_result = raw_decorator(_wrapped)
                        self._run_async_call(apply_result)
                return fn

            async def _subscribe_later() -> None:
                resolved = await self._await_raw_watcher()
                late_subscribe = getattr(resolved, "subscribe", None)
                if callable(late_subscribe):
                    late_decorator = late_subscribe(on=on)
                    if inspect.isawaitable(late_decorator):
                        late_decorator = await late_decorator
                    apply_result = late_decorator(_wrapped)
                    if inspect.isawaitable(apply_result):
                        await apply_result

            if self._raw_watcher_task is not None:
                self._run_async_call(_subscribe_later())
            return fn

        return _decorator


class SdkBusList(Generic[TRecord]):
    def __init__(
        self,
        items: list[TRecord],
        *,
        namespace: str,
        record_factory: type[_RecordFactory[TRecord]],
        host_ctx: object,
        raw_list: object | None = None,
    ) -> None:
        self.items = items
        self._namespace = namespace
        self._record_factory = record_factory
        self._host_ctx = host_ctx
        self._raw_list = raw_list

    @classmethod
    def from_raw(
        cls,
        raw_list: object,
        *,
        namespace: str,
        record_factory: type[_RecordFactory[TRecord]],
        host_ctx: object,
    ) -> "SdkBusList[TRecord]":
        items = [record_factory.from_raw(item) for item in _iter_raw_items(raw_list)]
        return cls(items, namespace=namespace, record_factory=record_factory, host_ctx=host_ctx, raw_list=raw_list)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> TRecord:
        return self.items[index]

    def count(self) -> int:
        return len(self.items)

    def size(self) -> int:
        return len(self.items)

    def dump(self) -> list[dict[str, Any]]:
        dumped: list[dict[str, Any]] = []
        for item in self.items:
            dumper = getattr(item, "dump", None)
            dumped.append(dumper() if callable(dumper) else {"value": str(item)})
        return dumped

    def dump_records(self) -> list[dict[str, Any]]:
        return self.dump()

    def explain(self) -> str:
        explainer = getattr(self._raw_list, "explain", None)
        if callable(explainer):
            return str(explainer())
        return f"SdkBusList(namespace={self._namespace!r}, count={len(self.items)})"

    def trace_tree_dump(self) -> dict[str, Any] | None:
        dumper = getattr(self._raw_list, "trace_tree_dump", None)
        if callable(dumper):
            value = dumper()
            return value if isinstance(value, dict) else {"trace": str(value)}
        return {"namespace": self._namespace, "count": len(self.items)}

    def _wrap_raw(self, raw_list: object) -> "SdkBusList[TRecord]":
        return SdkBusList.from_raw(
            raw_list,
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    @staticmethod
    def _item_value(item: object, field: str) -> object:
        return _mapping_get(item, field)

    def _local_filter(self, filters: dict[str, Any], *, strict: bool = True) -> "SdkBusList[TRecord]":
        def _matches(item: TRecord) -> bool:
            for key, expected in filters.items():
                if key == "strict":
                    continue
                try:
                    if key.endswith("_min"):
                        actual = self._item_value(item, key[:-4])
                        if actual is None or actual < expected:
                            return False
                        continue
                    if key.endswith("_max"):
                        actual = self._item_value(item, key[:-4])
                        if actual is None or actual > expected:
                            return False
                        continue
                    actual = self._item_value(item, key)
                    if actual != expected:
                        return False
                except Exception:
                    if strict:
                        raise
                    return False
            return True

        return SdkBusList(
            [item for item in self.items if _matches(item)],
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    @staticmethod
    def _raw_filter_accepts_kwargs(raw_filter: Callable[..., object], kwargs: dict[str, Any]) -> bool:
        try:
            signature = inspect.signature(raw_filter)
        except (TypeError, ValueError):
            return True
        parameters = signature.parameters.values()
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters):
            return True
        accepted = {param.name for param in parameters}
        return "strict" in accepted and all(key in accepted for key in kwargs)

    def filter(self, flt: object | None = None, *, strict: bool = True, **kwargs: Any) -> "SdkBusList[TRecord]":
        if flt is not None and not callable(flt):
            raise TypeError("filter predicate must be callable")

        if callable(flt) and not kwargs:
            return SdkBusList(
                [item for item in self.items if flt(item)],
                namespace=self._namespace,
                record_factory=self._record_factory,
                host_ctx=self._host_ctx,
            )

        raw_filter = getattr(self._raw_list, "filter", None)
        base_filtered: SdkBusList[TRecord]
        if callable(raw_filter) and (flt is None or kwargs):
            if self._raw_filter_accepts_kwargs(raw_filter, kwargs):
                base_filtered = self._wrap_raw(raw_filter(strict=strict, **kwargs))
            else:
                base_filtered = self._local_filter(kwargs, strict=strict)
        else:
            base_filtered = self._local_filter(kwargs, strict=strict)

        if callable(flt):
            return SdkBusList(
                [item for item in base_filtered.items if flt(item)],
                namespace=self._namespace,
                record_factory=self._record_factory,
                host_ctx=self._host_ctx,
                raw_list=getattr(base_filtered, "_raw_list", None),
            )
        return base_filtered

    def where(self, predicate: Callable[[TRecord], bool]) -> "SdkBusList[TRecord]":
        return self.filter(predicate)

    def where_in(self, field: str, values: Iterable[object]) -> "SdkBusList[TRecord]":
        raw_where_in = getattr(self._raw_list, "where_in", None)
        if callable(raw_where_in):
            return self._wrap_raw(raw_where_in(field, values))
        value_set = set(values)
        return SdkBusList(
            [item for item in self.items if self._item_value(item, field) in value_set],
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    def limit(self, size: int) -> "SdkBusList[TRecord]":
        raw_limit = getattr(self._raw_list, "limit", None)
        if callable(raw_limit):
            return self._wrap_raw(raw_limit(size))
        return SdkBusList(
            list(self.items[:size]),
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    @staticmethod
    def _dedupe_key(item: object) -> str:
        key_fn = getattr(item, "key", None)
        if callable(key_fn):
            return str(key_fn())
        return str(item)

    def _log_fallback_error(self, operation: str, error: Exception) -> None:
        logger = getattr(self._host_ctx, "logger", None)
        debug = getattr(logger, "debug", None)
        if not callable(debug):
            return
        try:
            debug(f"sdk_v2 bus fallback for {self._namespace}.{operation}: {error}")
        except Exception:
            return

    def __add__(self, other: "SdkBusList[TRecord]") -> "SdkBusList[TRecord]":
        raw_add = getattr(self._raw_list, "__add__", None)
        if callable(raw_add) and getattr(other, "_raw_list", None) is not None:
            try:
                return self._wrap_raw(raw_add(other._raw_list))
            except Exception as error:
                self._log_fallback_error("__add__", error)
        merged: dict[str, TRecord] = {}
        for item in [*self.items, *other.items]:
            merged[self._dedupe_key(item)] = item
        return SdkBusList(
            list(merged.values()),
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    def __and__(self, other: "SdkBusList[TRecord]") -> "SdkBusList[TRecord]":
        raw_and = getattr(self._raw_list, "__and__", None)
        if callable(raw_and) and getattr(other, "_raw_list", None) is not None:
            try:
                return self._wrap_raw(raw_and(other._raw_list))
            except Exception as error:
                self._log_fallback_error("__and__", error)
        other_keys = {self._dedupe_key(item) for item in other.items}
        return SdkBusList(
            [item for item in self.items if self._dedupe_key(item) in other_keys],
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    def watch(
        self,
        ctx: object | None = None,
        *,
        bus: str | None = None,
        debounce_ms: float = 0.0,
    ) -> SdkBusWatcher[TRecord]:
        watcher_factory = getattr(self._raw_list, "watch", None)
        if not callable(watcher_factory):
            raise TypeError("watch() is not available for this bus list")
        host_ctx = getattr(ctx, "_host_ctx", ctx) if ctx is not None else self._host_ctx
        raw_watcher = watcher_factory(host_ctx, bus=bus, debounce_ms=debounce_ms)
        return SdkBusWatcher(
            raw_watcher,
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )


class _SdkBusNamespace(Generic[TRecord]):
    def __init__(
        self,
        raw_namespace: object,
        *,
        namespace: str,
        record_factory: type[_RecordFactory[TRecord]],
        host_ctx: object,
    ) -> None:
        self._raw_namespace = raw_namespace
        self._namespace = namespace
        self._record_factory = record_factory
        self._host_ctx = host_ctx

    def _empty_list(self) -> SdkBusList[TRecord]:
        return SdkBusList(
            [],
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    def _wrap_result(self, value: object) -> object:
        if isinstance(value, Err):
            return value
        if isinstance(value, Ok):
            return Ok(self._wrap_result(value.value))
        if isinstance(value, Mapping):
            return value
        return SdkBusList.from_raw(
            value,
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
        )

    def _call(self, name: str, **kwargs: Any) -> object:
        method = getattr(self._raw_namespace, name, None)
        if not callable(method):
            return self._empty_list()
        result = method(**kwargs)
        if inspect.isawaitable(result):
            async def _await_result() -> object:
                return self._wrap_result(await result)

            return _await_result()
        return self._wrap_result(result)


class SdkMessagesBus(_SdkBusNamespace[SdkBusMessageRecord]):
    def __init__(self, raw_namespace: object, *, host_ctx: object) -> None:
        super().__init__(raw_namespace, namespace="messages", record_factory=SdkBusMessageRecord, host_ctx=host_ctx)

    def get(self, **kwargs: Any) -> object:
        return self._call("get", **kwargs)


class SdkEventsBus(_SdkBusNamespace[SdkBusEventRecord]):
    def __init__(self, raw_namespace: object, *, host_ctx: object) -> None:
        super().__init__(raw_namespace, namespace="events", record_factory=SdkBusEventRecord, host_ctx=host_ctx)

    def get(self, **kwargs: Any) -> object:
        return self._call("get", **kwargs)


class SdkLifecycleBus(_SdkBusNamespace[SdkBusLifecycleRecord]):
    def __init__(self, raw_namespace: object, *, host_ctx: object) -> None:
        super().__init__(raw_namespace, namespace="lifecycle", record_factory=SdkBusLifecycleRecord, host_ctx=host_ctx)

    def get(self, **kwargs: Any) -> object:
        return self._call("get", **kwargs)


class SdkConversationsBus(_SdkBusNamespace[SdkBusConversationRecord]):
    def __init__(self, raw_namespace: object, *, host_ctx: object) -> None:
        super().__init__(raw_namespace, namespace="conversations", record_factory=SdkBusConversationRecord, host_ctx=host_ctx)

    def get(self, **kwargs: Any) -> object:
        return self._call("get", **kwargs)

    def get_by_id(self, conversation_id: str, max_count: int = 10, timeout: float = 5.0) -> object:
        method = getattr(self._raw_namespace, "get_by_id", None)
        if callable(method):
            result = method(conversation_id, max_count=max_count, timeout=timeout)
            if inspect.isawaitable(result):
                async def _await_result() -> object:
                    return self._wrap_result(await result)

                return _await_result()
            return self._wrap_result(result)
        return self.get(conversation_id=conversation_id, max_count=max_count, timeout=timeout)


class SdkMemoryBus(_SdkBusNamespace[SdkBusMemoryRecord]):
    def __init__(self, raw_namespace: object, *, host_ctx: object) -> None:
        super().__init__(raw_namespace, namespace="memory", record_factory=SdkBusMemoryRecord, host_ctx=host_ctx)

    def _wrap_result(self, value: object) -> object:
        if isinstance(value, Err):
            return value
        if isinstance(value, Ok):
            return Ok(self._wrap_result(value.value))
        if isinstance(value, Mapping):
            return value
        items = [SdkBusMemoryRecord.from_raw(item) for item in _iter_raw_items(value)]
        return SdkBusList(
            items,
            namespace=self._namespace,
            record_factory=self._record_factory,
            host_ctx=self._host_ctx,
            raw_list=value,
        )

    def get(self, *, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> object:
        method = getattr(self._raw_namespace, "get", None)
        if not callable(method):
            return self._empty_list()
        result = method(bucket_id=bucket_id, limit=limit, timeout=timeout)
        if inspect.isawaitable(result):
            async def _await_result() -> object:
                return self._wrap_result(await result)

            return _await_result()
        return self._wrap_result(result)


class SdkBusContext:
    """SDK v2-owned view over the host-provided bus namespaces."""

    def __init__(self, raw_bus: object, *, host_ctx: object) -> None:
        self._raw_bus = raw_bus
        self._host_ctx = host_ctx
        self.messages = SdkMessagesBus(getattr(raw_bus, "messages", object()), host_ctx=host_ctx)
        self.events = SdkEventsBus(getattr(raw_bus, "events", object()), host_ctx=host_ctx)
        self.lifecycle = SdkLifecycleBus(getattr(raw_bus, "lifecycle", object()), host_ctx=host_ctx)
        self.conversations = SdkConversationsBus(getattr(raw_bus, "conversations", object()), host_ctx=host_ctx)
        self.memory = SdkMemoryBus(getattr(raw_bus, "memory", object()), host_ctx=host_ctx)


def ensure_sdk_bus_context(raw_bus: object | None, *, host_ctx: object) -> SdkBusContext:
    if isinstance(raw_bus, SdkBusContext):
        return raw_bus
    return SdkBusContext(raw_bus, host_ctx=host_ctx)


__all__ = [
    "SdkBusContext",
    "SdkBusConversationRecord",
    "SdkBusDelta",
    "SdkBusEventRecord",
    "SdkBusLifecycleRecord",
    "SdkBusList",
    "SdkBusMemoryRecord",
    "SdkBusMessageRecord",
    "SdkBusWatcher",
    "SdkConversationsBus",
    "SdkEventsBus",
    "SdkLifecycleBus",
    "SdkMemoryBus",
    "SdkMessagesBus",
    "ensure_sdk_bus_context",
]

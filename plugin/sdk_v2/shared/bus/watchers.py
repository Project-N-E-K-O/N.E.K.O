"""Watcher bus facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Protocol

from plugin.sdk_v2.public.bus.watchers import Watchers as _ImplWatchers
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Ok, Result

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList, BusRecord


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None]: ...


@dataclass(frozen=True)
class BusListDelta:
    kind: str
    added: tuple[object, ...]
    removed: tuple[str, ...]
    changed: tuple[object, ...]
    current: BusList[object]


class BusListWatcherCore:
    @staticmethod
    def _item_key(item: object) -> str:
        key_fn = getattr(item, "key", None)
        if callable(key_fn):
            return str(key_fn())
        return str(getattr(item, "id", item))

    @staticmethod
    def _item_version(item: object) -> int | None:
        version_fn = getattr(item, "version", None)
        if callable(version_fn):
            try:
                return version_fn()
            except Exception:
                return None
        value = getattr(item, "rev", None)
        return value if isinstance(value, int) else None

    def __init__(self, watcher_id: str, bus_client: "Watchers", channel: str):
        self.watcher_id = watcher_id
        self.bus_client = bus_client
        self.channel = channel
        self._callbacks: list = []
        self._delta_callbacks: list = []
        self._started = False
        self._current = BusList[object]([])
        self._seen: dict[str, int | None] = {}

    def subscribe(self, callback):
        if callable(callback):
            self._callbacks.append(callback)
        return self

    def subscribe_delta(self, callback):
        if callable(callback):
            self._delta_callbacks.append(callback)
        return self

    def start(self):
        self._started = True
        return self

    async def start_async(self):
        return self.start()

    def stop(self):
        self._started = False
        return self

    async def stop_async(self):
        return self.stop()

    async def poll(self) -> Result[list[BusEvent], Exception]:
        result = await self.bus_client.poll(self.channel)
        if not self.bus_client._is_ok(result):
            return result
        if self._started:
            for event in result.value:
                for callback in list(self._callbacks):
                    try:
                        out = callback(event)
                        if hasattr(out, "__await__"):
                            await out
                    except Exception:
                        continue
        return result

    async def poll_delta(self) -> Result[BusListDelta, Exception]:
        polled = await self.poll()
        if not self.bus_client._is_ok(polled):
            return polled
        if self.channel.startswith("records:") or self.channel == "records":
            current_map = {self._item_key(item): item for item in self._current.items}
            added_items: list[object] = []
            changed_items: list[object] = []
            removed_ids: list[str] = []
            for event in polled.value:
                payload = dict(getattr(event, "payload", {}) or {})
                op = str(payload.get("op", ""))
                record_payload = payload.get("record", {})
                record = BusRecord.from_raw(record_payload if isinstance(record_payload, dict) else {})
                record_key = record.key()
                record_version = record.version()
                if op == "delete":
                    if record_key in current_map:
                        current_map.pop(record_key, None)
                        self._seen.pop(record_key, None)
                        removed_ids.append(record.id)
                elif op == "change":
                    current_map[record_key] = record
                    self._seen[record_key] = record_version
                    changed_items.append(record)
                else:
                    current_map[record_key] = record
                    self._seen[record_key] = record_version
                    added_items.append(record)
            self._current = BusList(list(current_map.values()))
            delta = BusListDelta(kind=("change" if changed_items else "remove" if removed_ids else "append" if added_items else "noop"), added=tuple(added_items), removed=tuple(removed_ids), changed=tuple(changed_items), current=self._current)
        else:
            current_map = {self._item_key(item): item for item in self._current.items}
            added_items: list[object] = []
            changed_items: list[object] = []
            for event in polled.value:
                key = self._item_key(event)
                version = self._item_version(event)
                previous_version = self._seen.get(key)
                if key not in current_map:
                    added_items.append(event)
                elif previous_version != version:
                    changed_items.append(event)
                current_map[key] = event
                self._seen[key] = version
            self._current = BusList(list(current_map.values()))
            delta = BusListDelta(kind="change" if changed_items else "append" if added_items else "noop", added=tuple(added_items), removed=(), changed=tuple(changed_items), current=self._current)
        if self._started:
            for callback in list(self._delta_callbacks):
                try:
                    out = callback(delta)
                    if hasattr(out, "__await__"):
                        await out
                except Exception:
                    continue
        return Ok(delta)


class BusListWatcher(BusListWatcherCore):
    pass


class Watchers(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup_impl(_ImplWatchers, _transport, namespace="watchers")

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, Exception]:
        channel_ok = self._require_non_empty_str("channel", channel)
        if not self._is_ok(channel_ok):
            return channel_ok
        if not callable(handler):
            return self._type_error("handler", "callable")
        return await self._call("bus.watchers.watch", self._impl.watch, channel_ok, handler, options=options, timeout=timeout)

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        watcher_ok = self._require_non_empty_str("watcher_id", watcher_id)
        if not self._is_ok(watcher_ok):
            return watcher_ok
        return await self._call("bus.watchers.unwatch", self._impl.unwatch, watcher_ok, timeout=timeout)

    async def poll(self, channel: str, *, timeout: float = 5.0) -> Result[list[BusEvent], Exception]:
        channel_ok = self._require_non_empty_str("channel", channel)
        if not self._is_ok(channel_ok):
            return channel_ok
        return await self._call("bus.watchers.poll", self._impl.poll, channel_ok, timeout=timeout)


def list_subscription(watcher: BusListWatcher, *, on: str = "poll") -> BusListWatcher:
    if on == "start":
        watcher.start()
    return watcher


async def list_subscription_async(watcher: BusListWatcher, *, on: str = "poll") -> BusListWatcher:
    if on == "start":
        await watcher.start_async()
    return watcher


list_Subscription = list_subscription

__all__ = ["Watchers", "WatchEventHandler", "BusListDelta", "BusListWatcher", "BusListWatcherCore", "list_subscription", "list_subscription_async", "list_Subscription"]

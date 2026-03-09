"""Watcher bus facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Protocol

from plugin.sdk_v2.public.bus.watchers import Watchers as _ImplWatchers
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result

from ._client_base import BusClientBase
from .types import BusEvent, BusList


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None]: ...


@dataclass(frozen=True)
class BusListDelta:
    kind: str
    added: tuple[object, ...]
    removed: tuple[str, ...]
    changed: tuple[object, ...]
    current: BusList[object]


class BusListWatcher:
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
        if isinstance(result, Err):
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
        if isinstance(polled, Err):
            return polled
        if self.channel.startswith("records:") or self.channel == "records":
            from .types import BusRecord
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


class Watchers(BusClientBase):
    def __init__(self, _transport=None):
        super().__init__(_transport, namespace="watchers")
        self._impl = _ImplWatchers(self._transport)
        self._state = self._impl._state

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, Exception]:
        if not isinstance(channel, str) or channel.strip() == "":
            return Err(ValueError("channel must be non-empty"))
        if not callable(handler):
            return Err(TypeError("handler must be callable"))
        return await self._forward_bus_result("bus.watchers.watch", self._impl.watch, channel, handler, options=options, timeout=timeout)

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        if not isinstance(watcher_id, str) or watcher_id.strip() == "":
            return Err(ValueError("watcher_id must be non-empty"))
        return await self._forward_bus_result("bus.watchers.unwatch", self._impl.unwatch, watcher_id, timeout=timeout)

    async def poll(self, channel: str, *, timeout: float = 5.0) -> Result[list[BusEvent], Exception]:
        if not isinstance(channel, str) or channel.strip() == "":
            return Err(ValueError("channel must be non-empty"))
        return await self._forward_bus_result("bus.watchers.poll", self._impl.poll, channel, timeout=timeout)


def list_subscription(watcher: BusListWatcher, *, on: str = "poll") -> BusListWatcher:
    if on == "start":
        watcher.start()
    return watcher


async def list_subscription_async(watcher: BusListWatcher, *, on: str = "poll") -> BusListWatcher:
    if on == "start":
        await watcher.start_async()
    return watcher


list_Subscription = list_subscription

__all__ = ["Watchers", "WatchEventHandler", "BusListDelta", "BusListWatcher", "list_subscription", "list_subscription_async", "list_Subscription"]

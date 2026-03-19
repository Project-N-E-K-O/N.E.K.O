"""Watcher bus facade."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Protocol, TypeAlias

from plugin.sdk_v2.shared.bus._state import _Watcher
from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import BusErrorLike, InvalidArgumentError

from ._facade import BusFacadeMixin
from .types import BusEvent, BusList, BusRecord


WatchCallbackResult: TypeAlias = Awaitable[None] | None
logger = logging.getLogger(__name__)


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> WatchCallbackResult: ...


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
    def _item_version(item: object) -> float | int | None:
        version_fn = getattr(item, "version", None)
        if callable(version_fn):
            try:
                value = version_fn()
                return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            except Exception:
                return None
        value = getattr(item, "rev", None)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def __init__(
        self,
        watcher_id: str,
        bus_client: "Watchers",
        channel: str,
        *,
        current_items: list[object] | None = None,
        seen: dict[str, float | int | None] | None = None,
        auto_register: bool = False,
    ):
        self.watcher_id = watcher_id
        self.bus_client = bus_client
        self.channel = channel
        self._callbacks: list = []
        self._delta_callbacks: list = []
        self._started = False
        self._auto_register = auto_register
        self._registered = False
        self._current = BusList[object](list(current_items or []))
        self._seen: dict[str, float | int | None] = dict(seen or {})

    def _ensure_registered(self) -> None:
        if not self._auto_register or self._registered:
            return
        state = getattr(self.bus_client, "_state", None)
        watchers = getattr(state, "watchers", None)
        if not isinstance(watchers, dict):
            return
        watchers.setdefault(self.watcher_id, _Watcher(id=self.watcher_id, channel=self.channel, handler=lambda _event: None))
        self._registered = True

    def subscribe(self, callback):
        if callable(callback):
            self._callbacks.append(callback)
        return self

    def subscribe_delta(self, callback):
        if callable(callback):
            self._delta_callbacks.append(callback)
        return self

    def start(self):
        self._ensure_registered()
        self._started = True
        return self

    def stop(self):
        self._started = False
        if self._auto_register:
            state = getattr(self.bus_client, "_state", None)
            watchers = getattr(state, "watchers", None)
            if isinstance(watchers, dict):
                watchers.pop(self.watcher_id, None)
            self._registered = False
        return self

    async def poll(self) -> Result[list[BusEvent], BusErrorLike]:
        self._ensure_registered()
        result = await self.bus_client.poll(self.channel, watcher_id=self.watcher_id)
        if not self.bus_client._is_ok(result):
            return result
        if self._started:
            for event in result.value:
                for callback in list(self._callbacks):
                    try:
                        out = callback(event)
                        if hasattr(out, "__await__"):
                            await out
                    except Exception as error:
                        logger.debug(
                            "watcher callback failed callback=%r event=%r error=%s",
                            callback,
                            event,
                            error,
                            exc_info=True,
                        )
                        continue
        return result

    async def poll_delta(self) -> Result[BusListDelta, BusErrorLike]:
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
                        removed_ids.append(record_key)
                elif op == "change":
                    existed = record_key in current_map
                    current_map[record_key] = record
                    self._seen[record_key] = record_version
                    if existed:
                        changed_items.append(record)
                    else:
                        added_items.append(record)
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
                    logger.exception("watcher delta callback failed callback=%r delta=%r", callback, delta)
                    continue
        return Ok(delta)


class BusListWatcher(BusListWatcherCore):
    pass


class Watchers(BusFacadeMixin):
    def __init__(self, _transport=None):
        self._setup(_transport, namespace="watchers")

    async def _do_watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, BusErrorLike]:
        """Create a watcher; `options` and `timeout` are reserved for API compatibility."""
        _ = options, timeout
        trimmed_channel = channel.strip() if isinstance(channel, str) else ""
        if trimmed_channel == "":
            return Err(InvalidArgumentError("channel must be non-empty"))
        watcher_id = self._state.next_id("watcher")
        self._state.watchers[watcher_id] = _Watcher(id=watcher_id, channel=trimmed_channel, handler=handler)
        return Ok(watcher_id)

    async def _do_unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, BusErrorLike]:
        return Ok(self._state.watchers.pop(watcher_id, None) is not None)

    async def _do_poll(
        self,
        channel: str,
        *,
        watcher_id: str | None = None,
        timeout: float = 5.0,
    ) -> Result[list[BusEvent], BusErrorLike]:
        """Poll queued events for a channel."""
        if watcher_id is not None:
            watcher = self._state.watchers.get(watcher_id)
            if watcher is None:
                return Ok([])
            if watcher.channel not in {channel, "*"}:
                return Ok([])
            items = list(watcher.queue)
            watcher.queue.clear()
            return Ok(items)

        items: list[BusEvent] = []
        for watcher in self._state.watchers.values():
            if watcher.channel == channel or watcher.channel == "*":
                items.extend(watcher.queue)
                watcher.queue.clear()
        return Ok(items)

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, BusErrorLike]:
        channel_ok = self._require_non_empty_str("channel", channel)
        if not self._is_ok(channel_ok):
            return channel_ok
        if not callable(handler):
            return self._type_error("handler", "callable")
        return await self._call("bus.watchers.watch", self._do_watch, channel_ok.value, handler, options=options, timeout=timeout)

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, BusErrorLike]:
        watcher_ok = self._require_non_empty_str("watcher_id", watcher_id)
        if not self._is_ok(watcher_ok):
            return watcher_ok
        return await self._call("bus.watchers.unwatch", self._do_unwatch, watcher_ok.value, timeout=timeout)

    async def poll(self, channel: str, *, watcher_id: str | None = None, timeout: float = 5.0) -> Result[list[BusEvent], BusErrorLike]:
        channel_ok = self._require_non_empty_str("channel", channel)
        if not self._is_ok(channel_ok):
            return channel_ok
        return await self._call("bus.watchers.poll", self._do_poll, channel_ok.value, watcher_id=watcher_id, timeout=timeout)


def list_subscription(watcher: BusListWatcher, *, on: str = "poll") -> BusListWatcher:
    if on == "start":
        watcher.start()
    return watcher


__all__ = ["Watchers", "WatchEventHandler", "BusListDelta", "BusListWatcher", "BusListWatcherCore", "list_subscription"]

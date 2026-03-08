"""Watcher bus contracts."""

from __future__ import annotations

from typing import Awaitable, Protocol

from plugin.sdk_v2.shared.core.types import JsonObject
from plugin.sdk_v2.shared.models import Result

from ._client_base import BusClientBase
from .types import BusEvent


class WatchEventHandler(Protocol):
    def __call__(self, event: BusEvent) -> Awaitable[None]: ...


class Watchers(BusClientBase):
    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.bus.watchers not implemented")

    async def watch(self, channel: str, handler: WatchEventHandler, *, options: JsonObject | None = None, timeout: float = 5.0) -> Result[str, Exception]:
        raise NotImplementedError

    async def unwatch(self, watcher_id: str, *, timeout: float = 5.0) -> Result[bool, Exception]:
        raise NotImplementedError

    async def poll(self, channel: str, *, timeout: float = 5.0) -> Result[list[BusEvent], Exception]:
        raise NotImplementedError


__all__ = ["Watchers", "WatchEventHandler"]

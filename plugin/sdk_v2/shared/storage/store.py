"""KV store contracts for SDK v2 shared storage."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonValue
from plugin.sdk_v2.shared.models import Result


class PluginStore:
    """Async-only KV store contract."""

    def __init__(self, *args: object, **kwargs: object):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.storage.store not implemented")

    async def get(self, key: str, default: JsonValue | None = None) -> Result[JsonValue | None, Exception]:
        raise NotImplementedError

    async def set(self, key: str, value: JsonValue) -> Result[None, Exception]:
        raise NotImplementedError

    async def delete(self, key: str) -> Result[bool, Exception]:
        raise NotImplementedError

    async def exists(self, key: str) -> Result[bool, Exception]:
        raise NotImplementedError

    async def keys(self, prefix: str = "") -> Result[list[str], Exception]:
        raise NotImplementedError

    async def clear(self) -> Result[int, Exception]:
        raise NotImplementedError


__all__ = ["PluginStore"]

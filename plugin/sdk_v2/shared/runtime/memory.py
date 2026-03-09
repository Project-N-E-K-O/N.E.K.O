"""Shared facade for memory runtime."""

from plugin.sdk_v2.public.runtime.memory import MemoryClient as _ImplMemoryClient
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result


class MemoryClient:
    """Async-first memory facade."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._impl = _ImplMemoryClient(_ctx)

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[JsonObject | JsonValue | None, Exception]:
        return await self._impl.query(bucket_id, query, timeout=timeout)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[JsonObject], Exception]:
        return await self._impl.get(bucket_id, limit=limit, timeout=timeout)


__all__ = ["MemoryClient"]

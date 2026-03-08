from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

from ._deprecation import warn_sync_deprecated

if TYPE_CHECKING:
    from plugin.sdk.bus.memory import MemoryList
    from .types import PluginContextProtocol


@dataclass
class MemoryClient:
    ctx: "PluginContextProtocol"
    _bus_client: Optional[Any] = None

    def _bus(self) -> Any:
        if self._bus_client is None:
            # Lazy import to avoid circular import during plugin bootstrap.
            from plugin.sdk.bus.memory import MemoryClient as BusMemoryClient

            # Type: ignore because bus client expects concrete PluginContext with internal methods
            self._bus_client = BusMemoryClient(self.ctx)  # type: ignore[arg-type]
        return self._bus_client

    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> "MemoryList":
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            warn_sync_deprecated("MemoryClient", "get", "get_async")
        return self._bus().get(bucket_id=bucket_id, limit=limit, timeout=timeout)

    async def get_async(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> "MemoryList":
        bus = self._bus()
        if hasattr(bus, "get_async"):
            return await bus.get_async(bucket_id=bucket_id, limit=limit, timeout=timeout)
        return await asyncio.to_thread(bus.get, bucket_id=bucket_id, limit=limit, timeout=timeout)

    def query(self, lanlan_name: str, query: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        warn_sync_deprecated("MemoryClient", "query", "query_async")
        if not hasattr(self.ctx, "query_memory"):
            raise RuntimeError("ctx.query_memory is not available")
        result = self.ctx.query_memory(lanlan_name=lanlan_name, query=query, timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    async def query_async(self, lanlan_name: str, query: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        if hasattr(self.ctx, "query_memory_async"):
            result = await self.ctx.query_memory_async(lanlan_name=lanlan_name, query=query, timeout=timeout)
            return result if isinstance(result, dict) else {"result": result}
        if hasattr(self.ctx, "query_memory"):
            result = await asyncio.to_thread(
                self.ctx.query_memory,
                lanlan_name=lanlan_name,
                query=query,
                timeout=timeout,
            )
            return result if isinstance(result, dict) else {"result": result}
        raise RuntimeError("ctx.query_memory(_async) is not available")

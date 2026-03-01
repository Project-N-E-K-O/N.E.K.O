from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

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
        return self._bus().get(bucket_id=bucket_id, limit=limit, timeout=timeout)

    def query(self, lanlan_name: str, query: str, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "query_memory"):
            raise RuntimeError("ctx.query_memory is not available")
        result = self.ctx.query_memory(lanlan_name=lanlan_name, query=query, timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result

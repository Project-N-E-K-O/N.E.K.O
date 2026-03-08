"""Memory query contracts for SDK v2 shared runtime."""

from __future__ import annotations

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Result


class MemoryClient:
    """Async-only memory client contract."""

    def __init__(self, _ctx: PluginContextProtocol):
        raise NotImplementedError("sdk_v2 contract-only facade: shared.runtime.memory not implemented")

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[JsonObject | JsonValue | None, Exception]:
        raise NotImplementedError

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[JsonObject], Exception]:
        raise NotImplementedError


__all__ = ["MemoryClient"]

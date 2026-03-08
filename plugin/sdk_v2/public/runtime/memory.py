"""Internal memory runtime implementation for SDK v2."""

from __future__ import annotations

from typing import Any

from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result


class MemoryClient:
    """Async-first memory client with best-effort integration to the host context."""

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx

    @staticmethod
    def _coerce_query_result(value: object) -> JsonObject | JsonValue | None:
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            return value
        return {"result": str(value)}

    @staticmethod
    def _coerce_records(value: object) -> list[JsonObject]:
        if hasattr(value, "dump_records") and callable(value.dump_records):
            dumped = value.dump_records()
            if isinstance(dumped, list):
                return [item for item in dumped if isinstance(item, dict)]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[JsonObject | JsonValue | None, Exception]:
        try:
            query_memory_async = getattr(self._ctx, "query_memory_async", None)
            query_memory = getattr(self._ctx, "query_memory", None)
            if callable(query_memory_async):
                result = await query_memory_async(bucket_id, query, timeout=timeout)
            elif callable(query_memory):
                result = query_memory(bucket_id, query, timeout=timeout)
                if hasattr(result, "__await__"):
                    result = await result
            else:
                return Err(RuntimeError("ctx.query_memory_async is not available"))
            return Ok(self._coerce_query_result(result))
        except Exception as error:
            return Err(error)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[JsonObject], Exception]:
        try:
            bus = getattr(self._ctx, "bus", None)
            memory_bus = getattr(bus, "memory", None) if bus is not None else None
            if memory_bus is not None:
                getter_async = getattr(memory_bus, "get_async", None)
                getter = getattr(memory_bus, "get", None)
                if callable(getter_async):
                    result = await getter_async(bucket_id=bucket_id, limit=limit, timeout=timeout)
                    return Ok(self._coerce_records(result))
                if callable(getter):
                    result = getter(bucket_id=bucket_id, limit=limit, timeout=timeout)
                    if hasattr(result, "__await__"):
                        result = await result
                    return Ok(self._coerce_records(result))
            return Err(RuntimeError("ctx.bus.memory.get_async is not available"))
        except Exception as error:
            return Err(error)


__all__ = ["MemoryClient"]

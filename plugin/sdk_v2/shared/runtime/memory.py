"""Shared facade for memory runtime."""

from __future__ import annotations

import inspect
from typing import Any, cast

from plugin.sdk_v2.shared.core.context import ensure_sdk_context
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Ok, Result
from plugin.sdk_v2.shared.models.exceptions import CapabilityUnavailableError, InvalidArgumentError, SdkError, TransportError

MemoryErrorLike = InvalidArgumentError | CapabilityUnavailableError | TransportError


class MemoryClient:
    """Async-first memory facade with validation and host integration."""

    def __init__(self, plugin_ctx: PluginContextProtocol):
        self.plugin_ctx = ensure_sdk_context(plugin_ctx)

    @staticmethod
    def _validate_bucket_id(bucket_id: str) -> Result[None, InvalidArgumentError]:
        if not isinstance(bucket_id, str) or bucket_id.strip() == "":
            return Err(InvalidArgumentError("bucket_id must be non-empty"))
        return _OK_NONE

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, InvalidArgumentError]:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            return Err(InvalidArgumentError("timeout must be > 0"))
        return _OK_NONE

    @staticmethod
    def _coerce_query_result(value: object) -> JsonObject | JsonValue | None:
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
            return value
        return {"result": str(value)}

    @staticmethod
    def _coerce_records(value: object) -> list[JsonObject]:
        dump_records = getattr(value, "dump_records", None)
        if callable(dump_records):
            dumped = dump_records()
            if isinstance(dumped, list):
                return [cast(JsonObject, item) for item in dumped if isinstance(item, dict)]
        if isinstance(value, list):
            return [cast(JsonObject, item) for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_impl_error(
        error: Exception,
        *,
        op_name: str,
        bucket_id: str = "",
        timeout: float = 0,
    ) -> MemoryErrorLike:
        if isinstance(error, (CapabilityUnavailableError, TransportError)):
            return error
        if isinstance(error, SdkError):
            return TransportError(str(error), op_name=op_name, bucket_id=bucket_id, timeout=timeout)
        return TransportError(str(error), op_name=op_name, bucket_id=bucket_id, timeout=timeout)

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[JsonObject | JsonValue | None, MemoryErrorLike]:
        bucket_ok = self._validate_bucket_id(bucket_id)
        if isinstance(bucket_ok, Err):
            return cast(Result[JsonObject | JsonValue | None, MemoryErrorLike], bucket_ok)
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[JsonObject | JsonValue | None, MemoryErrorLike], timeout_ok)
        if not isinstance(query, str) or query.strip() == "":
            return cast(Result[JsonObject | JsonValue | None, MemoryErrorLike], Err(InvalidArgumentError("query must be non-empty")))
        host_ctx = getattr(self.plugin_ctx, "_host_ctx", self.plugin_ctx)
        query_memory = getattr(host_ctx, "query_memory", None)
        if query_memory is None:
            return Err(
                CapabilityUnavailableError(
                    "plugin_ctx.query_memory is not available",
                    op_name="memory.query",
                    capability="plugin_ctx.query_memory",
                    bucket_id=bucket_id,
                    timeout=timeout,
                )
            )
        try:
            result = await query_memory(bucket_id, query, timeout=timeout)
            return Ok(self._coerce_query_result(result))
        except Exception as error:
            return cast(
                Result[JsonObject | JsonValue | None, MemoryErrorLike],
                Err(self._normalize_impl_error(error, op_name="memory.query", bucket_id=bucket_id, timeout=timeout)),
            )

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[JsonObject], MemoryErrorLike]:
        bucket_ok = self._validate_bucket_id(bucket_id)
        if isinstance(bucket_ok, Err):
            return cast(Result[list[JsonObject], MemoryErrorLike], bucket_ok)
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return cast(Result[list[JsonObject], MemoryErrorLike], timeout_ok)
        if type(limit) is not int or limit <= 0:
            return cast(Result[list[JsonObject], MemoryErrorLike], Err(InvalidArgumentError("limit must be > 0")))
        try:
            host_ctx = getattr(self.plugin_ctx, "_host_ctx", self.plugin_ctx)
            raw_bus = getattr(host_ctx, "bus", None)
            raw_memory_bus = getattr(raw_bus, "memory", None) if raw_bus is not None else None
            raw_get = getattr(raw_memory_bus, "get", None) if raw_memory_bus is not None else None
            if callable(raw_get):
                bus = getattr(self.plugin_ctx, "bus", None)
                memory_bus = getattr(bus, "memory", None) if bus is not None else None
                result = memory_bus.get(bucket_id=bucket_id, limit=limit, timeout=timeout)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, Err):
                    error = result.error if isinstance(result.error, Exception) else TransportError(str(result.error), op_name="memory.get", bucket_id=bucket_id, timeout=timeout)
                    return cast(Result[list[JsonObject], MemoryErrorLike], Err(self._normalize_impl_error(error, op_name="memory.get", bucket_id=bucket_id, timeout=timeout)))
                if isinstance(result, Ok):
                    result = result.value
                return Ok(self._coerce_records(result))
            return Err(
                CapabilityUnavailableError(
                    "plugin_ctx.bus.memory.get is not available",
                    op_name="memory.get",
                    capability="plugin_ctx.bus.memory.get",
                    bucket_id=bucket_id,
                    timeout=timeout,
                )
            )
        except Exception as error:
            return cast(
                Result[list[JsonObject], MemoryErrorLike],
                Err(self._normalize_impl_error(error, op_name="memory.get", bucket_id=bucket_id, timeout=timeout)),
            )

_OK_NONE = Ok(None)

__all__ = ["MemoryClient"]

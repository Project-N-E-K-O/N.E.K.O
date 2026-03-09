"""Shared facade for memory runtime."""

from __future__ import annotations

from plugin.sdk_v2.public.runtime.memory import MemoryClient as _ImplMemoryClient
from plugin.sdk_v2.shared.core.types import JsonObject, JsonValue, PluginContextProtocol
from plugin.sdk_v2.shared.models import Err, Result


class MemoryClient:
    """Async-first memory facade.

    This layer keeps lightweight validation and error normalization in `shared`,
    while the concrete host integration remains in `public.runtime.memory`.
    """

    def __init__(self, _ctx: PluginContextProtocol):
        self._ctx = _ctx
        self._impl = _ImplMemoryClient(_ctx)

    @staticmethod
    def _validate_bucket_id(bucket_id: str) -> Result[None, Exception]:
        if not isinstance(bucket_id, str) or bucket_id.strip() == "":
            return Err(ValueError("bucket_id must be non-empty"))
        return _OK_NONE

    @staticmethod
    def _validate_timeout(timeout: float) -> Result[None, Exception]:
        if timeout <= 0:
            return Err(ValueError("timeout must be > 0"))
        return _OK_NONE

    async def query(self, bucket_id: str, query: str, *, timeout: float = 5.0) -> Result[JsonObject | JsonValue | None, Exception]:
        bucket_ok = self._validate_bucket_id(bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        if not isinstance(query, str) or query.strip() == "":
            return Err(ValueError("query must be non-empty"))
        try:
            return await self._impl.query(bucket_id, query, timeout=timeout)
        except Exception as error:
            return Err(error)

    async def get(self, bucket_id: str, *, limit: int = 20, timeout: float = 5.0) -> Result[list[JsonObject], Exception]:
        bucket_ok = self._validate_bucket_id(bucket_id)
        if isinstance(bucket_ok, Err):
            return bucket_ok
        timeout_ok = self._validate_timeout(timeout)
        if isinstance(timeout_ok, Err):
            return timeout_ok
        if limit <= 0:
            return Err(ValueError("limit must be > 0"))
        try:
            return await self._impl.get(bucket_id, limit=limit, timeout=timeout)
        except Exception as error:
            return Err(error)


from plugin.sdk_v2.shared.models import Ok
_OK_NONE = Ok(None)

__all__ = ["MemoryClient"]

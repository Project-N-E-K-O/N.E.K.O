from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Coroutine, Dict, Optional, Sequence, Union

if TYPE_CHECKING:
    from plugin.core.context import PluginContext

from .types import BusList, BusRecord


@dataclass(frozen=True)
class MemoryRecord(BusRecord):
    bucket_id: str = "default"

    @staticmethod
    def from_raw(raw: Dict[str, Any], *, bucket_id: str) -> "MemoryRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"event": raw}
        ts = payload.get("_ts")
        timestamp = None
        try:
            if ts is not None:
                timestamp = float(ts)
        except Exception:
            timestamp = None

        typ = str(payload.get("type") or "UNKNOWN")
        plugin_id = payload.get("plugin_id")
        if plugin_id is not None:
            plugin_id = str(plugin_id)

        source = payload.get("source")
        if source is not None:
            source = str(source)

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except Exception:
            priority = 0

        content = payload.get("content")
        if content is not None:
            content = str(content)

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return MemoryRecord(
            kind="memory",
            type=typ,
            timestamp=timestamp,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            bucket_id=bucket_id,
        )

    def dump(self) -> Dict[str, Any]:
        base = BusRecord.dump(self)
        base["bucket_id"] = self.bucket_id
        return base


class MemoryList(BusList[MemoryRecord]):
    def __init__(
        self,
        items: Sequence[MemoryRecord],
        *,
        bucket_id: str = "default",
        ctx: Optional[Any] = None,
        trace: Optional[Sequence[Any]] = None,
        plan: Optional[Any] = None,
    ):
        super().__init__(items, ctx=ctx, trace=trace, plan=plan)
        self.bucket_id = bucket_id

    def _clone_from(self, source: BusList[MemoryRecord]) -> "MemoryList":
        return MemoryList(
            source.dump_records(),
            bucket_id=self.bucket_id,
            ctx=getattr(source, "_ctx", None),
            trace=getattr(source, "_trace", None),
            plan=getattr(source, "_plan", None),
        )

    def filter(self, *args: Any, **kwargs: Any) -> "MemoryList":
        filtered = super().filter(*args, **kwargs)
        return self._clone_from(filtered)

    def where(self, predicate: Any) -> "MemoryList":
        filtered = super().where(predicate)
        return self._clone_from(filtered)

    def limit(self, n: int) -> "MemoryList":
        limited = super().limit(n)
        return self._clone_from(limited)


@dataclass
class MemoryClient:
    ctx: "PluginContext"
    
    def _is_in_event_loop(self) -> bool:
        """Detect whether execution is inside an event loop."""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _params(self, bucket_id: str, limit: int) -> dict:
        if not isinstance(bucket_id, str) or not bucket_id:
            raise ValueError("bucket_id is required")
        return {"bucket_id": bucket_id, "limit": limit}

    @staticmethod
    def _records(result: Any, bucket_id: str) -> MemoryList:
        history = result.get("history", []) if isinstance(result, dict) else result
        history = history if isinstance(history, list) else []
        return MemoryList([
            MemoryRecord.from_raw(item if isinstance(item, dict) else {"event": item}, bucket_id=bucket_id)
            for item in history
        ], bucket_id=bucket_id)

    def get_sync(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        result = self.ctx._send_request_and_wait(
            method_name="bus.memory.get", request_type="USER_CONTEXT_GET",
            request_data=self._params(bucket_id, limit), timeout=timeout, wrap_result=False,
        )
        return self._records(result, bucket_id)

    async def get_async(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        result = await self.ctx._send_request_and_wait_async(
            method_name="bus.memory.get", request_type="USER_CONTEXT_GET",
            request_data=self._params(bucket_id, limit), timeout=timeout, wrap_result=False,
        )
        return self._records(result, bucket_id)

    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Union[MemoryList, Coroutine[Any, Any, MemoryList]]:
        """Adaptive version: detect the execution environment and choose synchronous or asynchronous execution.
        
        Returns:
            Return a coroutine inside an event loop, otherwise a MemoryList.
        """
        if self._is_in_event_loop():
            return self.get_async(bucket_id=bucket_id, limit=limit, timeout=timeout)
        return self.get_sync(bucket_id=bucket_id, limit=limit, timeout=timeout)

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from plugin.core.state import state

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
        base = super().dump()
        base["bucket_id"] = self.bucket_id
        return base


class MemoryList(BusList[MemoryRecord]):
    def __init__(self, items: Sequence[MemoryRecord], *, bucket_id: str):
        super().__init__(items)
        self.bucket_id = bucket_id

    def filter(self, *args: Any, **kwargs: Any) -> "MemoryList":
        filtered = super().filter(*args, **kwargs)
        return MemoryList(filtered.dump_records(), bucket_id=self.bucket_id)

    def where(self, predicate: Any) -> "MemoryList":
        filtered = super().where(predicate)
        return MemoryList(filtered.dump_records(), bucket_id=self.bucket_id)

    def limit(self, n: int) -> "MemoryList":
        limited = super().limit(n)
        return MemoryList(limited.dump_records(), bucket_id=self.bucket_id)


@dataclass
class MemoryClient:
    ctx: Any

    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.memory.get")

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        if not isinstance(bucket_id, str) or not bucket_id:
            raise ValueError("bucket_id is required")

        request_id = str(uuid.uuid4())
        request = {
            "type": "USER_CONTEXT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": request_id,
            "bucket_id": bucket_id,
            "limit": int(limit),
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send USER_CONTEXT_GET request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        history: List[Any] = []
        while time.time() - start_time < timeout:
            response = state.get_plugin_response(request_id)
            if response is None:
                time.sleep(check_interval)
                continue

            if not isinstance(response, dict):
                time.sleep(check_interval)
                continue

            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("history"), list):
                history = result.get("history")
            elif isinstance(result, list):
                history = result
            else:
                history = []
            break

        else:
            orphan_response = None
            try:
                orphan_response = state.get_plugin_response(request_id)
            except Exception:
                orphan_response = None
            if orphan_response is not None and hasattr(self.ctx, "logger"):
                try:
                    self.ctx.logger.warning(
                        f"[PluginContext] Timeout reached, but response was found (likely delayed). "
                        f"Cleaned up orphan response for req_id={request_id}"
                    )
                except Exception:
                    pass
            raise TimeoutError(f"USER_CONTEXT_GET timed out after {timeout}s")

        records: List[MemoryRecord] = []
        for item in history:
            if isinstance(item, dict):
                records.append(MemoryRecord.from_raw(item, bucket_id=bucket_id))
            else:
                records.append(MemoryRecord.from_raw({"event": item}, bucket_id=bucket_id))

        return MemoryList(records, bucket_id=bucket_id)

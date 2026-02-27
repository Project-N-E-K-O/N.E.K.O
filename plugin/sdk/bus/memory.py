from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union, Coroutine

from plugin.core.state import state
from plugin.settings import PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS

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
    ctx: "PluginContext"
    
    def _is_in_event_loop(self) -> bool:
        """检测当前是否在事件循环中运行"""
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def get_sync(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        """同步版本:获取内存数据"""
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

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        request_id = str(uuid.uuid4())
        request = {
            "type": "USER_CONTEXT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": request_id,
            "bucket_id": bucket_id,
            "limit": int(limit),
            "timeout": float(timeout),
        }
        history: List[Any] = []

        if zmq_client is not None:
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
            except Exception:
                resp = None
            if not isinstance(resp, dict):
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.memory.get] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"USER_CONTEXT_GET over ZeroMQ timed out or failed after {timeout}s")

            if resp.get("error"):
                raise RuntimeError(str(resp.get("error")))

            result = resp.get("result")
            if isinstance(result, dict):
                items = result.get("history")
                if isinstance(items, list):
                    history = items
                else:
                    history = []
            elif isinstance(result, list):
                history = result
            else:
                history = []
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send USER_CONTEXT_GET request: {e}") from e

            start_time = time.time()
            check_interval = 0.01
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
                if isinstance(result, dict):
                    items = result.get("history")
                    if isinstance(items, list):
                        history = items
                    else:
                        history = []
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
                if PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS and orphan_response is not None and hasattr(self.ctx, "logger"):
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
    
    async def get_async(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> MemoryList:
        """异步版本:获取内存数据"""
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

        zmq_client = getattr(self.ctx, "_zmq_ipc_client", None)

        request_id = str(uuid.uuid4())
        request = {
            "type": "USER_CONTEXT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": request_id,
            "bucket_id": bucket_id,
            "limit": int(limit),
            "timeout": float(timeout),
        }
        history: List[Any] = []

        if zmq_client is not None:
            try:
                resp = zmq_client.request(request, timeout=float(timeout))
            except Exception:
                resp = None
            if not isinstance(resp, dict):
                if hasattr(self.ctx, "logger"):
                    try:
                        self.ctx.logger.warning("[bus.memory.get] ZeroMQ IPC failed; raising exception (no fallback)")
                    except Exception:
                        pass
                raise TimeoutError(f"USER_CONTEXT_GET over ZeroMQ timed out or failed after {timeout}s")

            if resp.get("error"):
                raise RuntimeError(str(resp.get("error")))

            result = resp.get("result")
            if isinstance(result, dict):
                items = result.get("history")
                if isinstance(items, list):
                    history = items
                else:
                    history = []
            elif isinstance(result, list):
                history = result
            else:
                history = []
        else:
            try:
                plugin_comm_queue.put(request, timeout=timeout)
            except Exception as e:
                raise RuntimeError(f"Failed to send USER_CONTEXT_GET request: {e}") from e

            start_time = asyncio.get_event_loop().time()
            check_interval = 0.01
            while asyncio.get_event_loop().time() - start_time < timeout:
                response = state.get_plugin_response(request_id)
                if response is None:
                    await asyncio.sleep(check_interval)
                    continue

                if not isinstance(response, dict):
                    await asyncio.sleep(check_interval)
                    continue

                if response.get("error"):
                    raise RuntimeError(str(response.get("error")))

                result = response.get("result")
                if isinstance(result, dict):
                    items = result.get("history")
                    if isinstance(items, list):
                        history = items
                    else:
                        history = []
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
                if PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS and orphan_response is not None and hasattr(self.ctx, "logger"):
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
    
    def get(self, bucket_id: str, limit: int = 20, timeout: float = 5.0) -> Union[MemoryList, Coroutine[Any, Any, MemoryList]]:
        """智能版本:自动检测执行环境,选择同步或异步执行方式
        
        Returns:
            在事件循环中返回协程,否则返回 MemoryList
        """
        if self._is_in_event_loop():
            return self.get_async(bucket_id=bucket_id, limit=limit, timeout=timeout)
        return self.get_sync(bucket_id=bucket_id, limit=limit, timeout=timeout)

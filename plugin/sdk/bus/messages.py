from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from plugin.core.state import state

from .types import BusList, BusRecord


@dataclass(frozen=True)
class MessageRecord(BusRecord):
    message_id: Optional[str] = None
    message_type: Optional[str] = None
    description: Optional[str] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "MessageRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"content": raw}

        # Prefer ISO timestamp if provided; keep a best-effort float timestamp for filtering.
        ts_raw = payload.get("timestamp") or payload.get("time")
        timestamp: Optional[float] = None
        if isinstance(ts_raw, (int, float)):
            timestamp = float(ts_raw)

        plugin_id = payload.get("plugin_id")
        plugin_id = str(plugin_id) if plugin_id is not None else None

        source = payload.get("source")
        source = str(source) if source is not None else None

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except Exception:
            priority = 0

        content = payload.get("content")
        content = str(content) if content is not None else None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        message_id = payload.get("message_id")
        message_id = str(message_id) if message_id is not None else None

        message_type = payload.get("message_type")
        message_type = str(message_type) if message_type is not None else None

        description = payload.get("description")
        description = str(description) if description is not None else None

        # Use message_type as record type to align filtering with actual content type.
        record_type = str(message_type or payload.get("type") or "MESSAGE")

        return MessageRecord(
            kind="message",
            type=record_type,
            timestamp=timestamp,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            message_id=message_id,
            message_type=message_type,
            description=description,
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["message_id"] = self.message_id
        base["message_type"] = self.message_type
        base["description"] = self.description
        return base


class MessageList(BusList[MessageRecord]):
    def __init__(self, items: Sequence[MessageRecord], *, plugin_id: Optional[str] = None):
        super().__init__(items)
        self.plugin_id = plugin_id

    def filter(self, *args: Any, **kwargs: Any) -> "MessageList":
        filtered = super().filter(*args, **kwargs)
        return MessageList(filtered.dump_records(), plugin_id=self.plugin_id)

    def where(self, predicate: Any) -> "MessageList":
        filtered = super().where(predicate)
        return MessageList(filtered.dump_records(), plugin_id=self.plugin_id)

    def limit(self, n: int) -> "MessageList":
        limited = super().limit(n)
        return MessageList(limited.dump_records(), plugin_id=self.plugin_id)


@dataclass
class MessageClient:
    ctx: Any

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        priority_min: Optional[int] = None,
        timeout: float = 5.0,
    ) -> MessageList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.messages.get")

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        req_id = str(uuid.uuid4())
        pid_norm: Optional[str] = None
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        else:
            pid_norm = None

        if pid_norm == "":
            pid_norm = None

        request = {
            "type": "MESSAGE_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "priority_min": priority_min,
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send MESSAGE_GET request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        messages: List[Any] = []
        while time.time() - start_time < timeout:
            # NOTE: 同步轮询等待响应；每次循环 sleep 一小段时间以避免占满 CPU。
            response = state.get_plugin_response(req_id)
            if response is None:
                time.sleep(check_interval)
                continue
            if not isinstance(response, dict):
                time.sleep(check_interval)
                continue
            if response.get("error"):
                raise RuntimeError(str(response.get("error")))

            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("messages"), list):
                messages = result.get("messages")
            elif isinstance(result, list):
                messages = result
            else:
                messages = []
            break
        else:
            raise TimeoutError(f"MESSAGE_GET timed out after {timeout}s")

        records: List[MessageRecord] = []
        for item in messages:
            if isinstance(item, dict):
                records.append(MessageRecord.from_raw(item))
            else:
                records.append(MessageRecord.from_raw({"content": item}))

        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)
        return MessageList(records, plugin_id=effective_plugin_id)

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from plugin.core.state import state

from .types import BusList, BusRecord


def _iso_to_ts(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


@dataclass(frozen=True)
class EventRecord(BusRecord):
    event_id: Optional[str] = None
    entry_id: Optional[str] = None
    args: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "EventRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"raw": raw}

        ev_type = payload.get("type")
        ev_type = str(ev_type) if ev_type is not None else "EVENT"

        ts = _iso_to_ts(payload.get("timestamp") or payload.get("received_at") or payload.get("time"))

        plugin_id = payload.get("plugin_id")
        plugin_id = str(plugin_id) if plugin_id is not None else None

        source = payload.get("source")
        source = str(source) if source is not None else None

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        entry_id = payload.get("entry_id")
        entry_id = str(entry_id) if entry_id is not None else None

        event_id = payload.get("trace_id") or payload.get("event_id")
        event_id = str(event_id) if event_id is not None else None

        args = payload.get("args")
        if not isinstance(args, dict):
            args = None

        content = payload.get("content")
        if content is None and entry_id:
            content = entry_id
        content = str(content) if content is not None else None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return EventRecord(
            kind="event",
            type=str(ev_type),
            timestamp=ts,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            event_id=event_id,
            entry_id=entry_id,
            args=args,
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["event_id"] = self.event_id
        base["entry_id"] = self.entry_id
        base["args"] = dict(self.args) if isinstance(self.args, dict) else self.args
        return base


class EventList(BusList[EventRecord]):
    def __init__(self, items: Sequence[EventRecord], *, plugin_id: Optional[str] = None):
        super().__init__(items)
        self.plugin_id = plugin_id

    def filter(self, *args: Any, **kwargs: Any) -> "EventList":
        filtered = super().filter(*args, **kwargs)
        return EventList(filtered.dump_records(), plugin_id=self.plugin_id)

    def where(self, predicate: Any) -> "EventList":
        filtered = super().where(predicate)
        return EventList(filtered.dump_records(), plugin_id=self.plugin_id)

    def limit(self, n: int) -> "EventList":
        limited = super().limit(n)
        return EventList(limited.dump_records(), plugin_id=self.plugin_id)


@dataclass
class EventClient:
    ctx: Any

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> EventList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.events.get")

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        req_id = str(uuid.uuid4())
        pid_norm: Optional[str]
        if isinstance(plugin_id, str):
            pid_norm = plugin_id.strip()
        else:
            pid_norm = None
        if pid_norm == "":
            pid_norm = None

        request = {
            "type": "EVENT_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send EVENT_GET request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
        events: List[Any] = []
        while time.time() - start_time < timeout:
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
            if isinstance(result, dict) and isinstance(result.get("events"), list):
                events = result.get("events")
            elif isinstance(result, list):
                events = result
            else:
                events = []
            break
        else:
            _ = state.get_plugin_response(req_id)
            raise TimeoutError(f"EVENT_GET timed out after {timeout}s")

        records: List[EventRecord] = []
        for item in events:
            if isinstance(item, dict):
                records.append(EventRecord.from_raw(item))
            else:
                records.append(EventRecord.from_raw({"raw": item}))

        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)

        return EventList(records, plugin_id=effective_plugin_id)

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
class LifecycleRecord(BusRecord):
    lifecycle_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "LifecycleRecord":
        payload = dict(raw) if isinstance(raw, dict) else {"raw": raw}

        typ = payload.get("type")
        typ = str(typ) if typ is not None else "lifecycle"

        ts = _iso_to_ts(payload.get("timestamp") or payload.get("time") or payload.get("at"))

        plugin_id = payload.get("plugin_id")
        plugin_id = str(plugin_id) if plugin_id is not None else None

        source = payload.get("source")
        source = str(source) if source is not None else None

        priority = payload.get("priority", 0)
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = 0

        content = payload.get("content")
        content = str(content) if content is not None else None

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        lifecycle_id = payload.get("lifecycle_id") or payload.get("trace_id")
        lifecycle_id = str(lifecycle_id) if lifecycle_id is not None else None

        detail = payload.get("detail")
        if not isinstance(detail, dict):
            detail = None

        return LifecycleRecord(
            kind="lifecycle",
            type=typ,
            timestamp=ts,
            plugin_id=plugin_id,
            source=source,
            priority=priority,
            content=content,
            metadata=metadata,
            raw=payload,
            lifecycle_id=lifecycle_id,
            detail=detail,
        )

    def dump(self) -> Dict[str, Any]:
        base = super().dump()
        base["lifecycle_id"] = self.lifecycle_id
        base["detail"] = dict(self.detail) if isinstance(self.detail, dict) else self.detail
        return base


class LifecycleList(BusList[LifecycleRecord]):
    def __init__(self, items: Sequence[LifecycleRecord], *, plugin_id: Optional[str] = None):
        super().__init__(items)
        self.plugin_id = plugin_id

    def filter(self, *args: Any, **kwargs: Any) -> "LifecycleList":
        filtered = super().filter(*args, **kwargs)
        return LifecycleList(filtered.dump_records(), plugin_id=self.plugin_id)

    def where(self, predicate: Any) -> "LifecycleList":
        filtered = super().where(predicate)
        return LifecycleList(filtered.dump_records(), plugin_id=self.plugin_id)

    def limit(self, n: int) -> "LifecycleList":
        limited = super().limit(n)
        return LifecycleList(limited.dump_records(), plugin_id=self.plugin_id)

    def merge(self, other: "LifecycleList") -> "LifecycleList":
        merged = super().merge(other)
        pid = self.plugin_id if self.plugin_id == other.plugin_id else "*"
        return LifecycleList(merged.dump_records(), plugin_id=pid)

    def __add__(self, other: "LifecycleList") -> "LifecycleList":
        return self.merge(other)

    def sort(self, **kwargs: Any) -> "LifecycleList":
        sorted_list = super().sort(**kwargs)
        return LifecycleList(sorted_list.dump_records(), plugin_id=self.plugin_id)

    def sorted(self, **kwargs: Any) -> "LifecycleList":
        return self.sort(**kwargs)


@dataclass
class LifecycleClient:
    ctx: Any

    def get(
        self,
        plugin_id: Optional[str] = None,
        max_count: int = 50,
        timeout: float = 5.0,
    ) -> LifecycleList:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.lifecycle.get")

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
            "type": "LIFECYCLE_GET",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "plugin_id": pid_norm,
            "max_count": int(max_count),
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send LIFECYCLE_GET request: {e}") from e

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
            raise TimeoutError(f"LIFECYCLE_GET timed out after {timeout}s")

        records: List[LifecycleRecord] = []
        for item in events:
            if isinstance(item, dict):
                records.append(LifecycleRecord.from_raw(item))
            else:
                records.append(LifecycleRecord.from_raw({"raw": item}))

        if pid_norm == "*":
            effective_plugin_id = "*"
        else:
            effective_plugin_id = pid_norm if pid_norm else getattr(self.ctx, "plugin_id", None)

        return LifecycleList(records, plugin_id=effective_plugin_id)

    def delete(self, lifecycle_id: str, timeout: float = 5.0) -> bool:
        if hasattr(self.ctx, "_enforce_sync_call_policy"):
            self.ctx._enforce_sync_call_policy("bus.lifecycle.delete")

        plugin_comm_queue = getattr(self.ctx, "_plugin_comm_queue", None)
        if plugin_comm_queue is None:
            raise RuntimeError(
                f"Plugin communication queue not available for plugin {getattr(self.ctx, 'plugin_id', 'unknown')}. "
                "This method can only be called from within a plugin process."
            )

        lid = str(lifecycle_id).strip() if lifecycle_id is not None else ""
        if not lid:
            raise ValueError("lifecycle_id is required")

        req_id = str(uuid.uuid4())
        request = {
            "type": "LIFECYCLE_DEL",
            "from_plugin": getattr(self.ctx, "plugin_id", ""),
            "request_id": req_id,
            "lifecycle_id": lid,
            "timeout": float(timeout),
        }

        try:
            plugin_comm_queue.put(request, timeout=timeout)
        except Exception as e:
            raise RuntimeError(f"Failed to send LIFECYCLE_DEL request: {e}") from e

        start_time = time.time()
        check_interval = 0.01
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
            if isinstance(result, dict):
                return bool(result.get("deleted"))
            return False

        _ = state.get_plugin_response(req_id)
        raise TimeoutError(f"LIFECYCLE_DEL timed out after {timeout}s")

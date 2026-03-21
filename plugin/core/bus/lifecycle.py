from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .types import BusRecord, parse_iso_timestamp
from ._client_base import _PluginBusList, BusDeletableClientBase


@dataclass(frozen=True, slots=True)
class LifecycleRecord(BusRecord):
    lifecycle_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

    @staticmethod
    def from_raw(raw: Dict[str, Any]) -> "LifecycleRecord":
        payload = raw if isinstance(raw, dict) else {"raw": raw}

        typ = payload.get("type")
        ts_raw = payload.get("timestamp")
        if ts_raw is None:
            ts_raw = payload.get("time")
        if ts_raw is None:
            ts_raw = payload.get("at")

        plugin_id = payload.get("plugin_id")
        source = payload.get("source")
        priority = payload.get("priority", 0)
        content = payload.get("content")
        metadata = payload.get("metadata")
        lifecycle_id = payload.get("lifecycle_id")
        if lifecycle_id is None:
            lifecycle_id = payload.get("trace_id")
        detail = payload.get("detail")

        ts = parse_iso_timestamp(ts_raw)
        priority_int = priority if isinstance(priority, int) else (int(priority) if isinstance(priority, (float, str)) and priority else 0)

        return LifecycleRecord(
            kind="lifecycle",
            type=typ if isinstance(typ, str) else (str(typ) if typ is not None else "lifecycle"),
            timestamp=ts,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id is not None else None),
            source=source if isinstance(source, str) else (str(source) if source is not None else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content is not None else None),
            metadata=metadata if isinstance(metadata, dict) else {},
            raw=payload,
            lifecycle_id=lifecycle_id if isinstance(lifecycle_id, str) else (str(lifecycle_id) if lifecycle_id is not None else None),
            detail=detail if isinstance(detail, dict) else None,
        )

    @staticmethod
    def from_index(index: Dict[str, Any], payload: Optional[Dict[str, Any]] = None) -> "LifecycleRecord":
        ts = index.get("timestamp")
        timestamp: Optional[float] = float(ts) if isinstance(ts, (int, float)) else None
        priority = index.get("priority")
        priority_int = priority if isinstance(priority, int) else (int(priority) if priority else 0)

        lifecycle_id = index.get("id")
        plugin_id = index.get("plugin_id")
        source = index.get("source")
        lc_type = index.get("type")

        detail = None
        content = None
        metadata: Dict[str, Any] = {}
        if payload:
            detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
            content = payload.get("content")
            meta_raw = payload.get("metadata")
            metadata = meta_raw if isinstance(meta_raw, dict) else {}

        return LifecycleRecord(
            kind="lifecycle",
            type=lc_type if isinstance(lc_type, str) else (str(lc_type) if lc_type else "lifecycle"),
            timestamp=timestamp,
            plugin_id=plugin_id if isinstance(plugin_id, str) else (str(plugin_id) if plugin_id else None),
            source=source if isinstance(source, str) else (str(source) if source else None),
            priority=priority_int,
            content=content if isinstance(content, str) else (str(content) if content else None),
            metadata=metadata,
            raw=payload or index,
            lifecycle_id=lifecycle_id if isinstance(lifecycle_id, str) else (str(lifecycle_id) if lifecycle_id else None),
            detail=detail,
        )

    def dump(self) -> Dict[str, Any]:
        base = BusRecord.dump(self)
        base["lifecycle_id"] = self.lifecycle_id
        base["detail"] = dict(self.detail) if isinstance(self.detail, dict) else self.detail
        return base


class LifecycleList(_PluginBusList[LifecycleRecord]):
    pass


class LifecycleClient(BusDeletableClientBase):
    _store_name = "lifecycle"
    _record_cls = LifecycleRecord
    _list_cls = LifecycleList
    _policy_prefix = "bus.lifecycle"
    _del_type = "LIFECYCLE_DEL"
    _id_field = "lifecycle_id"

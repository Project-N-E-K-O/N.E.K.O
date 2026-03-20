"""Shared facade for plugin state persistence."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from plugin.sdk_v2.shared.core.types import JsonObject, LoggerLike
from plugin.sdk_v2.shared.logging import get_plugin_logger
from plugin.sdk_v2.shared.models import Result
from plugin.sdk_v2.shared.models.exceptions import InvalidArgumentError, TransportError

from ._template import StorageResultTemplate

try:
    import ormsgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return _msgpack.packb(value)

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data)
except ImportError:  # pragma: no cover
    import msgpack as _msgpack  # type: ignore

    def _pack(value: object) -> bytes:
        return _msgpack.packb(value, use_bin_type=True)

    def _unpack(data: bytes) -> object:
        return _msgpack.unpackb(data, raw=False)


_TYPE_TAG = "__neko_type__"
_TYPE_VALUE = "__neko_value__"
EXTENDED_TYPES = (datetime, date, timedelta, Enum, set, frozenset, Path)


def _serialize_extended(value: Any) -> Any:
    if isinstance(value, datetime):
        return {_TYPE_TAG: "datetime", _TYPE_VALUE: value.isoformat()}
    if isinstance(value, date):
        return {_TYPE_TAG: "date", _TYPE_VALUE: value.isoformat()}
    if isinstance(value, timedelta):
        return {_TYPE_TAG: "timedelta", _TYPE_VALUE: value.total_seconds()}
    if isinstance(value, Enum):
        return {_TYPE_TAG: "enum", "enum_class": f"{value.__class__.__module__}.{value.__class__.__name__}", _TYPE_VALUE: value.value}
    if isinstance(value, set):
        return {_TYPE_TAG: "set", _TYPE_VALUE: list(value)}
    if isinstance(value, frozenset):
        return {_TYPE_TAG: "frozenset", _TYPE_VALUE: list(value)}
    if isinstance(value, Path):
        return {_TYPE_TAG: "path", _TYPE_VALUE: str(value)}
    return None


def _deserialize_extended(data: dict[str, Any]) -> Any:
    tag = data.get(_TYPE_TAG)
    value = data.get(_TYPE_VALUE)
    if tag == "datetime":
        return datetime.fromisoformat(value)
    if tag == "date":
        return date.fromisoformat(value)
    if tag == "timedelta":
        return timedelta(seconds=value)
    if tag == "set":
        return set(value) if isinstance(value, list) else set()
    if tag == "frozenset":
        return frozenset(value) if isinstance(value, list) else frozenset()
    if tag == "path":
        return Path(value)
    if tag == "enum":
        try:
            module_name, class_name = str(data.get("enum_class", "")).rsplit(".", 1)
            import importlib

            enum_class = getattr(importlib.import_module(module_name), class_name)
            return enum_class(value)
        except Exception:
            return value
    return None


class PluginStatePersistence(StorageResultTemplate):
    """Async-first plugin state persistence."""

    STATE_VERSION = 1

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: Path,
        logger: LoggerLike | None = None,
        backend: str = "off",
    ):
        normalized_backend = (backend or "off").lower()
        if normalized_backend not in {"file", "memory", "off"}:
            raise InvalidArgumentError("backend must be one of: file, memory, off")
        super().__init__(logger=logger or get_plugin_logger(plugin_id, "storage.state"))
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.backend = normalized_backend
        self._state_path = self.plugin_dir / ".plugin_state"
        self._memory_state: bytes | None = None

    def _serialize_value(self, key: str, value: Any, instance: object) -> Any:
        if hasattr(instance, "__freeze_serialize__"):
            custom = instance.__freeze_serialize__(key, value)  # type: ignore[attr-defined]
            if custom is not None:
                return custom
        extended = _serialize_extended(value)
        if extended is not None:
            return extended
        if isinstance(value, dict):
            return {k: self._serialize_value(f"{key}.{k}", v, instance) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(f"{key}[{i}]", v, instance) for i, v in enumerate(value)]
        if isinstance(value, tuple):
            return [self._serialize_value(f"{key}[{i}]", v, instance) for i, v in enumerate(value)]
        return value

    def _deserialize_value(self, key: str, value: Any, instance: object) -> Any:
        if isinstance(value, dict) and _TYPE_TAG in value:
            restored = _deserialize_extended(value)
            if hasattr(instance, "__freeze_deserialize__"):
                custom = instance.__freeze_deserialize__(key, restored)  # type: ignore[attr-defined]
                if custom is not None:
                    return custom
            return restored
        if hasattr(instance, "__freeze_deserialize__"):
            custom = instance.__freeze_deserialize__(key, value)  # type: ignore[attr-defined]
            if custom is not None:
                return custom
        if isinstance(value, dict):
            return {k: self._deserialize_value(f"{key}.{k}", v, instance) for k, v in value.items()}
        if isinstance(value, list):
            return [self._deserialize_value(f"{key}[{i}]", v, instance) for i, v in enumerate(value)]
        return value

    def _collect_attrs(self, instance: object) -> JsonObject:
        keys = list(getattr(instance, "__freezable__", []) or [])
        snapshot: JsonObject = {}
        for key in keys:
            if hasattr(instance, key):
                snapshot[key] = self._serialize_value(key, getattr(instance, key), instance)
        return snapshot

    def _collect_snapshot(self, instance: object) -> JsonObject:
        return self._collect_attrs(instance)

    def _restore_attrs(self, instance: object, snapshot: JsonObject) -> int:
        restored = 0
        for key, value in snapshot.items():
            setattr(instance, key, self._deserialize_value(key, value, instance))
            restored += 1
        return restored

    def _save_state(self, instance: object) -> bool:
        if self.backend == "off":
            return False
        payload = {
            "version": self.STATE_VERSION,
            "plugin_id": self.plugin_id,
            "saved_at": time.time(),
            "data": self._collect_snapshot(instance),
        }
        data = _pack(payload)
        if self.backend == "memory":
            self._memory_state = data
        else:
            self.plugin_dir.mkdir(parents=True, exist_ok=True)
            self._state_path.write_bytes(data)
        return True

    def _load_state(self, instance: object) -> bool:
        if self.backend == "off":
            return False
        data: bytes | None
        if self.backend == "memory":
            data = self._memory_state
            if data is None:
                return False
        else:
            if not self._state_path.exists():
                return False
            data = self._state_path.read_bytes()
        raw = _unpack(data)
        if not isinstance(raw, dict):
            return False
        snapshot = raw.get("data", {})
        if not isinstance(snapshot, dict):
            return False
        self._restore_attrs(instance, snapshot)
        return True

    def _clear_state(self) -> bool:
        if self.backend == "off":
            return False
        if self.backend == "memory":
            existed = self._memory_state is not None
            self._memory_state = None
            return existed
        if self._state_path.exists():
            self._state_path.unlink()
            return True
        return False

    def _snapshot_state(self) -> JsonObject:
        if self.backend == "off":
            return {}
        if self.backend == "memory":
            if self._memory_state is None:
                return {}
            raw = _unpack(self._memory_state)
        else:
            if not self._state_path.exists():
                return {}
            raw = _unpack(self._state_path.read_bytes())
        if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
            return raw["data"]
        return {}

    def _has_saved_state_local(self) -> bool:
        if self.backend == "off":
            return False
        if self.backend == "memory":
            return self._memory_state is not None
        return self._state_path.exists()

    def _read_state_info(self) -> JsonObject | None:
        if self.backend == "off":
            return None
        if not self._has_saved_state_local():
            return None
        if self.backend == "memory":
            size = len(self._memory_state or b"")
            return {"backend": "memory", "plugin_id": self.plugin_id, "size_bytes": size}
        try:
            stat = self._state_path.stat()
        except Exception:
            return None
        return {"backend": self.backend, "plugin_id": self.plugin_id, "path": str(self._state_path), "size_bytes": stat.st_size}

    async def save(self, instance: object) -> Result[bool, TransportError]:
        return await self._run_local_result("storage.state.save", self._save_state, instance)

    async def load(self, instance: object) -> Result[bool, TransportError]:
        return await self._run_local_result("storage.state.load", self._load_state, instance)

    async def clear(self) -> Result[bool, TransportError]:
        return await self._run_local_result("storage.state.clear", self._clear_state)

    async def snapshot(self) -> Result[JsonObject, TransportError]:
        return await self._run_local_result("storage.state.snapshot", self._snapshot_state)

    async def collect_attrs(self, instance: object) -> Result[JsonObject, TransportError]:
        return await self._run_local_result("storage.state.collect_attrs", self._collect_attrs, instance)

    async def restore_attrs(self, instance: object, snapshot: JsonObject) -> Result[int, TransportError]:
        return await self._run_local_result("storage.state.restore_attrs", self._restore_attrs, instance, snapshot)

    async def has_saved_state(self) -> Result[bool, TransportError]:
        return await self._run_local_result("storage.state.has_saved_state", self._has_saved_state_local)

    async def get_state_info(self) -> Result[JsonObject | None, TransportError]:
        return await self._run_local_result("storage.state.get_state_info", self._read_state_info)


__all__ = ["EXTENDED_TYPES", "PluginStatePersistence"]

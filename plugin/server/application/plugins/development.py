"""Exact development-directory registrations, separate from installation ownership.

All mutations are called under the existing cross-process plugin operation lock.
The thread lock also fences registry discovery against writes in this process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4

from plugin.core.entry_points import (
    describe_plugin_entry_directory_mismatch, normalize_plugin_entry_point,
)
from plugin.core.state import state
from plugin.neko_plugin_cli.core.plugin_source import load_plugin_source
from plugin.server.domain.errors import ServerDomainError
from plugin import settings

development_registry_lock = threading.RLock()


@dataclass(frozen=True)
class DevelopmentSnapshot:
    registration_id: str
    revision: int
    plugin_id: str
    source_dir: Path


def _error(message: str, code: str = "DEVELOPMENT_INVALID", status: int = 400) -> ServerDomainError:
    return ServerDomainError(code=code, message=message, status_code=status)


def _store_path() -> Path:
    return settings.get_plugin_state_root().parent / "plugin-development.json"


def _read_sync() -> dict:
    path = _store_path()
    if not path.exists():
        return {"enabled": False, "registrations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            raise ValueError("invalid settings")
        records = data["registrations"]
        if not isinstance(records, list):
            raise ValueError("invalid registrations")
        ids, paths, plugins = set(), set(), set()
        for item in records:
            if not isinstance(item, dict):
                raise ValueError("invalid registration")
            if item.keys() - {"registration_id", "revision", "plugin_id", "source_dir"}:
                raise ValueError("unknown registration fields")
            for key in ("registration_id", "plugin_id", "source_dir"):
                if not isinstance(item.get(key), str) or not item[key]:
                    raise ValueError(f"invalid {key}")
            if type(item.get("revision")) is not int or item["revision"] < 1:
                raise ValueError("invalid revision")
            if not Path(item["source_dir"]).is_absolute():
                raise ValueError("source path is not absolute")
            for seen, key in ((ids, "registration_id"), (paths, "source_dir"), (plugins, "plugin_id")):
                if item[key] in seen:
                    raise ValueError("duplicate registration")
                seen.add(item[key])
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _error(f"Cannot read development registrations: {exc}", "DEVELOPMENT_STORE_INVALID", 500) from exc


def _write_sync(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".plugin-development-", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def development_enabled_sync() -> bool:
    with development_registry_lock:
        return _read_sync()["enabled"]


def list_registration_records_sync() -> list[DevelopmentSnapshot]:
    with development_registry_lock:
        return [DevelopmentSnapshot(**{**item, "source_dir": Path(item["source_dir"])})
                for item in _read_sync()["registrations"]]


def registration_for_plugin_sync(plugin_id: str) -> DevelopmentSnapshot | None:
    try:
        record = next((item for item in list_registration_records_sync() if item.plugin_id == plugin_id), None)
    except ServerDomainError as exc:
        if exc.code != "DEVELOPMENT_STORE_INVALID":
            raise
        # A broken optional store must not disable known managed plugins, but
        # must never make an external/unknown source look like an ordinary one.
        with state.acquire_plugins_read_lock():
            meta = state.plugins.get(plugin_id)
            config_path = meta.get("config_path") if isinstance(meta, dict) else None
            is_development = isinstance(meta, dict) and (meta.get("source") == "development" or meta.get("development_ref"))
        if config_path and not is_development:
            path = Path(config_path).resolve()
            if path.name == "plugin.toml" and any(path.parent.parent == Path(root).resolve() for root in settings.PLUGIN_CONFIG_ROOTS):
                return None
        raise
    if record is None:
        # Removal in another worker (or manual store repair) does not erase
        # this process's provenance. Never reparse that cached source as ordinary.
        with state.acquire_plugins_read_lock():
            meta = state.plugins.get(plugin_id)
            is_development = isinstance(meta, dict) and (meta.get("source") == "development" or meta.get("development_ref"))
        if is_development:
            raise _error("Development registration changed; refresh and retry", "DEVELOPMENT_STALE", 409)
    return record


def validate_directory_sync(source_dir: str | Path, *, expected_id: str | None = None) -> dict:
    try:
        path = Path(source_dir).expanduser()
        if not path.is_absolute():
            raise ValueError("Select an absolute directory on the backend machine")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Source directory is unavailable")
        # Development must never confer installation ownership or overlap state.
        for root in (*settings.PLUGIN_CONFIG_ROOTS, settings.get_plugin_state_root()):
            root = Path(root).resolve()
            if path.is_relative_to(root) or root.is_relative_to(path):
                raise ValueError(f"Development source overlaps a managed plugin directory: {root}")
        source = load_plugin_source(path)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", source.plugin_id):
            raise ValueError("Invalid plugin ID")
        if expected_id is not None and source.plugin_id != expected_id:
            raise ValueError("Plugin ID changed; remove the association and register it again")
        entry = source.plugin_toml["plugin"].get("entry", "")
        if not isinstance(entry, str) or ":" not in entry:
            raise ValueError("plugin.entry must name a module and class")
        entry = normalize_plugin_entry_point(entry, config_path=path / "plugin.toml",
                                              builtin_plugin_root=settings.BUILTIN_PLUGIN_CONFIG_ROOT)
        mismatch = describe_plugin_entry_directory_mismatch(entry, config_path=path / "plugin.toml")
        if mismatch:
            raise ValueError(mismatch)
        module, class_name = entry.split(":", 1)
        parts = module.split(".")
        if (parts[0] != "plugins" or len(parts) < 2
                or not (parts[1].isidentifier() or re.fullmatch(r"[a-zA-Z0-9_-]+", parts[1]))
                or not all(p.isidentifier() for p in parts[2:]) or not class_name.isidentifier()):
            raise ValueError("Entry must use plugins.<directory>[.<module>]:<class>")
        relative = Path(*parts[2:]) if len(parts) > 2 else Path()
        module_file = path / relative
        candidates = [module_file / "__init__.py"]
        if len(parts) > 2:
            candidates.append(module_file.with_suffix(".py"))
        if not any(p.is_file() and p.resolve().is_relative_to(path) for p in candidates):
            raise ValueError(f"Entry Python source not found for {entry}")
        return {"plugin_id": source.plugin_id, "source_dir": str(path), "name": source.name,
                "version": source.version, "entry": entry, "error": None}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise _error(str(exc)) from exc


def _check_conflicts_sync(metadata: dict, *, excluding: str | None = None) -> None:
    path = Path(metadata["source_dir"])
    plugin_id = metadata["plugin_id"]
    # A new association cannot take ownership of an existing runtime ID,
    # even when its manifest is unreadable or lives outside managed roots.
    # Existing associations keep their identity while rebinding/refreshing.
    if excluding is None:
        with state.acquire_plugins_read_lock():
            registered = plugin_id in state.plugins
            meta = state.plugins.get(plugin_id)
            owner = meta.get("config_path", plugin_id) if isinstance(meta, dict) else plugin_id
        if registered:
            raise _error(f"Plugin ID already registered in runtime at {owner}", "DEVELOPMENT_CONFLICT", 409)
        with state.acquire_plugin_hosts_read_lock():
            has_host = plugin_id in state.plugin_hosts
        if has_host:
            raise _error(f"Plugin ID already has a runtime host: {plugin_id}", "DEVELOPMENT_CONFLICT", 409)
    for record in list_registration_records_sync():
        if record.registration_id != excluding and (
            record.plugin_id == plugin_id or record.source_dir == path
        ):
            raise _error(f"Plugin ID or directory already registered at {record.source_dir}", "DEVELOPMENT_CONFLICT", 409)
    for root in settings.PLUGIN_CONFIG_ROOTS:
        for manifest in Path(root).glob("*/plugin.toml"):
            try:
                source = load_plugin_source(manifest.parent)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if source.plugin_id == plugin_id:
                raise _error(f"Plugin ID already exists at {source.plugin_dir}", "DEVELOPMENT_CONFLICT", 409)


def inspect_directory_sync(source_dir: str) -> dict:
    with development_registry_lock:
        metadata = validate_directory_sync(source_dir)
        same = next((item for item in list_registration_records_sync()
                     if item.source_dir == Path(metadata["source_dir"])), None)
        _check_conflicts_sync(metadata, excluding=same.registration_id if same else None)
        return metadata


def register_directory_sync(source_dir: str) -> DevelopmentSnapshot:
    with development_registry_lock:
        data = _read_sync()
        if not data["enabled"]:
            raise _error("Enable development mode first", "DEVELOPMENT_DISABLED", 409)
        metadata = validate_directory_sync(source_dir)
        for item in list_registration_records_sync():
            if item.source_dir == Path(metadata["source_dir"]):
                validate_directory_sync(item.source_dir, expected_id=item.plugin_id)
                return item
        _check_conflicts_sync(metadata)
        record = DevelopmentSnapshot(uuid4().hex, 1, metadata["plugin_id"], Path(metadata["source_dir"]))
        data["registrations"].append({**asdict(record), "source_dir": str(record.source_dir)})
        _write_sync(data)
        return record


def require_registration_sync(registration_id: str, revision: int) -> DevelopmentSnapshot:
    record = next((item for item in list_registration_records_sync() if item.registration_id == registration_id), None)
    if record is None or record.revision != revision:
        raise _error("Development registration changed; refresh and retry", "DEVELOPMENT_STALE", 409)
    return record


def resolve_development_ref_sync(registration_id: str, revision: int) -> DevelopmentSnapshot:
    with development_registry_lock:
        record = require_registration_sync(registration_id, revision)
        if not development_enabled_sync():
            raise _error("Development mode is disabled", "DEVELOPMENT_DISABLED", 409)
        metadata = validate_directory_sync(record.source_dir, expected_id=record.plugin_id)
        _check_conflicts_sync(metadata, excluding=record.registration_id)
        return record


def validate_development_snapshot_sync(snapshot: DevelopmentSnapshot) -> None:
    if resolve_development_ref_sync(snapshot.registration_id, snapshot.revision) != snapshot:
        raise _error("Development source changed; retry", "DEVELOPMENT_STALE", 409)


class development_snapshot_guard_sync:
    """Fence validation and publication without mutating exception tracebacks."""

    def __init__(self, snapshots):
        self.snapshots = snapshots

    def __enter__(self):
        development_registry_lock.acquire()
        try:
            for snapshot in self.snapshots:
                validate_development_snapshot_sync(snapshot)
        except BaseException:
            development_registry_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        development_registry_lock.release()
        return False


def list_development_snapshots_sync() -> list[DevelopmentSnapshot]:
    with development_registry_lock:
        if not development_enabled_sync():
            return []
        return [resolve_development_ref_sync(item.registration_id, item.revision)
                for item in list_registration_records_sync()]


def registration_view_sync(record: DevelopmentSnapshot) -> dict:
    result = {**asdict(record), "source_dir": str(record.source_dir)}
    try:
        result.update(validate_directory_sync(record.source_dir, expected_id=record.plugin_id))
        _check_conflicts_sync(result, excluding=record.registration_id)
    except ServerDomainError as exc:
        result.update(name=record.plugin_id, version="", entry="", error=exc.message)
    return result


def development_view_sync() -> dict:
    with development_registry_lock:
        return {"enabled": development_enabled_sync(),
                "registrations": [registration_view_sync(item) for item in list_registration_records_sync()]}


def set_enabled_sync(enabled: bool) -> None:
    with development_registry_lock:
        data = _read_sync()
        if data["enabled"] != enabled:
            data["enabled"] = enabled
            for item in data["registrations"]:
                item["revision"] += 1
            _write_sync(data)


def remove_registration_sync(record: DevelopmentSnapshot) -> None:
    with development_registry_lock:
        require_registration_sync(record.registration_id, record.revision)
        data = _read_sync()
        data["registrations"] = [item for item in data["registrations"] if item["registration_id"] != record.registration_id]
        _write_sync(data)


def rebind_registration_sync(record: DevelopmentSnapshot, source_dir: str) -> DevelopmentSnapshot:
    with development_registry_lock:
        require_registration_sync(record.registration_id, record.revision)
        metadata = validate_directory_sync(source_dir, expected_id=record.plugin_id)
        _check_conflicts_sync(metadata, excluding=record.registration_id)
        updated = DevelopmentSnapshot(record.registration_id, record.revision + 1,
                                      record.plugin_id, Path(metadata["source_dir"]))
        data = _read_sync()
        data["registrations"] = [{**asdict(updated), "source_dir": str(updated.source_dir)}
                                 if item["registration_id"] == record.registration_id else item
                                 for item in data["registrations"]]
        _write_sync(data)
        return updated

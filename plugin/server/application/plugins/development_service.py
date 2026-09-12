"""Development lifecycle orchestration under the shared plugin operation lock."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from pathlib import Path

from plugin.core.state import state
from plugin.server.application.plugins import development as store
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import PLUGIN_SHUTDOWN_TIMEOUT, PROCESS_TERMINATE_TIMEOUT


def _record_runtime_failure_sync(record: store.DevelopmentSnapshot, error: Exception | None) -> None:
    """Store transient feedback in the existing registry, fenced to this association."""
    with store.development_registry_lock:
        try:
            store.require_registration_sync(record.registration_id, record.revision)
        except ServerDomainError:
            return
        reference = {"registration_id": record.registration_id, "revision": record.revision}
        with state.acquire_plugins_write_lock():
            meta = state.plugins.get(record.plugin_id)
            if meta is None:
                meta = {"id": record.plugin_id, "name": record.plugin_id, "source": "development",
                        "config_path": str(record.source_dir / "plugin.toml"), "development_ref": reference}
                state.plugins[record.plugin_id] = meta
            if not isinstance(meta, dict) or meta.get("development_ref") != reference:
                return
            if error is not None:
                meta["runtime_startup_state"] = "failed"
                meta["runtime_startup_error"] = error.message if isinstance(error, ServerDomainError) else str(error)
            elif meta.get("runtime_startup_state") == "failed":
                meta.pop("runtime_startup_state", None)
                meta.pop("runtime_startup_error", None)
        state.invalidate_snapshot_cache("plugins")


def _overlay_runtime_error_sync(result: dict) -> dict:
    # Source validation can fail while its old process is still alive. Expose
    # liveness separately so the UI can retain Stop alongside the source error.
    with state.acquire_plugin_hosts_read_lock():
        host = state.plugin_hosts.get(result["plugin_id"])
    try:
        result["runtime_alive"] = host is not None and host.is_alive()
    except Exception:
        result["runtime_alive"] = None
    with state.acquire_plugins_read_lock():
        meta = state.plugins.get(result["plugin_id"])
        reference = {"registration_id": result["registration_id"], "revision": result["revision"]}
        if (not result.get("error") and isinstance(meta, dict)
                and meta.get("development_ref") == reference
                and meta.get("runtime_startup_state") in {"failed", "degraded"}):
            result["error"] = meta.get("runtime_startup_error")
    return result


def registration_view_sync(record: store.DevelopmentSnapshot) -> dict:
    return _overlay_runtime_error_sync(store.registration_view_sync(record))


def development_view_sync() -> dict:
    result = store.development_view_sync()
    result["registrations"] = [_overlay_runtime_error_sync(item) for item in result["registrations"]]
    # A client waiting for sequential stops needs more than a single-stop deadline.
    # Include graceful exit, terminate/kill joins, communication/tool cleanup,
    # lock acquisition and registry/transport headroom. This is a wait estimate,
    # not a server cancellation deadline or a guarantee against stalled I/O.
    per_plugin = PLUGIN_SHUTDOWN_TIMEOUT + 2 * PROCESS_TERMINATE_TIMEOUT + 10.0
    seconds = env_seconds("NEKO_PLUGIN_OPERATION_WAIT_BUDGET", 20.0) + 60.0 + len(result["registrations"]) * per_plugin
    result["disable_timeout_ms"] = int(min(2_147_483_647, max(300_000, seconds * 1000)))
    return result


@serialized_plugin_operation
async def stop_ordinary_plugin(plugin_id: str, *, lifecycle_service=None) -> dict:
    """Legacy admin commands cannot identify a versioned development source."""
    if await asyncio.to_thread(store.registration_for_plugin_sync, plugin_id) is not None:
        raise store._error("Use the versioned HTTP lifecycle API to stop a development plugin",
                           "DEVELOPMENT_REFERENCE_REQUIRED", 409)
    if lifecycle_service is None:
        from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
        lifecycle_service = PluginLifecycleService()
    return await lifecycle_service.stop_plugin(plugin_id)


def preflight_development_sync(snapshot: store.DevelopmentSnapshot) -> None:
    """Validate current source before stopping a healthy process; do not import it."""
    store.validate_development_snapshot_sync(snapshot)
    _preflight_source_sync(snapshot.source_dir, snapshot.plugin_id)


def _preflight_source_sync(source_dir: Path, plugin_id: str) -> None:
    """Check a validated source, including a replacement not yet registered."""
    from plugin.core.registry import _parse_single_plugin_config
    from plugin.server.application.plugins.registry_service import _build_discovery_payload, logger

    ctx = _parse_single_plugin_config(source_dir / "plugin.toml", set(), logger)
    if ctx is None or ctx.pid != plugin_id:
        raise store._error("Development manifest could not be validated")
    payload = _build_discovery_payload(replace(ctx, enabled=True), plugin_id=ctx.pid)
    if payload.get("runtime_load_state") == "failed":
        raise store._error(str(payload.get("runtime_load_error_message")))
    # Syntax errors in plugin-owned modules should not kill the old instance.
    # Vendor dependencies keep their existing loader/validation policy.
    def walk_error(exc):
        raise store._error(str(exc)) from exc

    for root, directories, files in os.walk(source_dir, onerror=walk_error, followlinks=False):
        directories[:] = [name for name in directories if name not in {"vendor", ".venv", ".git", "__pycache__"}]
        for name in files:
            if not name.lower().endswith(".py"):
                continue
            path = Path(root) / name
            relative = path.relative_to(source_dir)
            if not path.resolve().is_relative_to(source_dir):
                raise store._error(f"Python source escapes the registered directory: {relative}")
            try:
                compile(path.read_bytes(), str(path), "exec")
            except (SyntaxError, OSError) as exc:
                raise store._error(str(exc)) from exc


def _has_owned_host_sync(record: store.DevelopmentSnapshot) -> bool:
    """Called under the operation lock; registry metadata may describe a new source."""
    from plugin.server.application.plugins.lifecycle_service import _get_plugin_host_sync
    host = _get_plugin_host_sync(record.plugin_id)
    if host is None:
        return False
    try:
        path = Path(host.config_path)
        if not path.is_absolute():
            raise ValueError("host path is not absolute")
        path = path.resolve()
        # Registrations already store canonical directories. Do not reinterpret
        # the saved source through a newly introduced directory symlink.
        if path == record.source_dir / "plugin.toml":
            return True
        if path.name == "plugin.toml" and any(path.parent.parent == Path(root).resolve()
                                              for root in store.settings.PLUGIN_CONFIG_ROOTS):
            return False
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    raise store._error("Cannot verify plugin host source; association was retained", "DEVELOPMENT_STOP_FAILED", 409)


async def _stop_if_present(record: store.DevelopmentSnapshot) -> None:
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    # All callers hold the same lifecycle lock through this check and stop.
    if await asyncio.to_thread(_has_owned_host_sync, record):
        result = await PluginLifecycleService().stop_plugin(record.plugin_id)
        if not result.get("success"):
            raise store._error("Plugin did not stop; association was retained", "DEVELOPMENT_STOP_FAILED", 409)


def _forget_metadata_sync(record: store.DevelopmentSnapshot) -> None:
    with state.acquire_plugins_write_lock():
        meta = state.plugins.get(record.plugin_id)
        if isinstance(meta, dict) and isinstance(meta.get("development_ref"), dict) and meta["development_ref"].get("registration_id") == record.registration_id:
            state.plugins.pop(record.plugin_id, None)
    state.invalidate_snapshot_cache("plugins")


@serialized_plugin_operation
async def set_development_enabled(enabled: bool) -> dict:
    if not enabled:
        for record in await asyncio.to_thread(store.list_registration_records_sync):
            await _stop_if_present(record)
            await asyncio.to_thread(store.require_registration_sync, record.registration_id, record.revision)
    await asyncio.to_thread(store.set_enabled_sync, enabled)
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    await PluginRegistryService().refresh_registry()
    return await asyncio.to_thread(development_view_sync)


@serialized_plugin_operation
async def register_development(source_dir: str) -> dict:
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    record = await asyncio.to_thread(store.register_directory_sync, source_dir)
    try:
        await PluginRegistryService().refresh_plugin(record.plugin_id)
        await asyncio.to_thread(store.validate_development_snapshot_sync, record)
        await PluginLifecycleService().start_plugin(record.plugin_id, persist_user_intent=True)
    except Exception as exc:
        # Registration is intentionally durable when loading fails.
        await asyncio.to_thread(_record_runtime_failure_sync, record, exc)
        result = await asyncio.to_thread(registration_view_sync, record)
        result["error"] = exc.message if isinstance(exc, ServerDomainError) else str(exc)
        return result
    await asyncio.to_thread(_record_runtime_failure_sync, record, None)
    return await asyncio.to_thread(registration_view_sync, record)


@serialized_plugin_operation
async def remove_development(registration_id: str, revision: int) -> dict:
    record = await asyncio.to_thread(store.require_registration_sync, registration_id, revision)
    await _stop_if_present(record)
    await asyncio.to_thread(store.remove_registration_sync, record)
    await asyncio.to_thread(_forget_metadata_sync, record)
    return {"success": True, "registration_id": registration_id}


@serialized_plugin_operation
async def rebind_development(registration_id: str, revision: int, source_dir: str) -> dict:
    record = await asyncio.to_thread(store.require_registration_sync, registration_id, revision)
    metadata = await asyncio.to_thread(preview_development_sync, source_dir, record.registration_id, record.revision)
    if await asyncio.to_thread(_has_owned_host_sync, record):
        # Preserve an existing instance on detectable replacement errors. With
        # no host, directory repair remains possible before dependencies work.
        await asyncio.to_thread(_preflight_source_sync, Path(metadata["source_dir"]), record.plugin_id)
    await asyncio.to_thread(store.require_registration_sync, record.registration_id, record.revision)
    await _stop_if_present(record)
    updated = await asyncio.to_thread(store.rebind_registration_sync, record, source_dir)
    from plugin.server.application.plugins.registry_service import PluginRegistryService
    await PluginRegistryService().refresh_plugin(updated.plugin_id)
    return await asyncio.to_thread(registration_view_sync, updated)


def preview_development_sync(source_dir: str, registration_id: str | None = None, revision: int | None = None) -> dict:
    if registration_id is None:
        return store.inspect_directory_sync(source_dir)
    with store.development_registry_lock:
        record = store.require_registration_sync(registration_id, revision)
        metadata = store.validate_directory_sync(source_dir, expected_id=record.plugin_id)
        store._check_conflicts_sync(metadata, excluding=record.registration_id)
        return metadata


@serialized_plugin_operation
async def development_lifecycle_action(plugin_id: str, action: str,
                                       registration_id: str | None, revision: int | None) -> dict:
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService
    record = await asyncio.to_thread(store.registration_for_plugin_sync, plugin_id)
    if record is None or record.registration_id != registration_id or record.revision != revision:
        raise store._error("Development registration changed; refresh and retry", "DEVELOPMENT_STALE", 409)
    service = PluginLifecycleService()
    if action == "stop":
        await _stop_if_present(record)
        return {"success": True, "plugin_id": plugin_id}
    try:
        await asyncio.to_thread(store.validate_development_snapshot_sync, record)
        if action == "reload":
            result = await service.reload_plugin(plugin_id)
        else:
            result = await service.start_plugin(plugin_id, persist_user_intent=True)
    except Exception as exc:
        await asyncio.to_thread(_record_runtime_failure_sync, record, exc)
        raise
    await asyncio.to_thread(_record_runtime_failure_sync, record, None)
    return result

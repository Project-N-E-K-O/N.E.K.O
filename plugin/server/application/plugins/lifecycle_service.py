from __future__ import annotations

import asyncio
import importlib
import re
import time as time_module
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastapi import HTTPException

from plugin._types.exceptions import PluginError
from plugin._types.models import PluginAuthor, PluginMeta
from plugin._types.version import SDK_VERSION
from plugin.core.host import PluginProcessHost
from plugin.core.registry import (
    _check_plugin_dependency,
    _extract_entries_preview,
    _parse_plugin_dependencies,
    _resolve_plugin_id_conflict,
    register_plugin,
    scan_static_metadata,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS, RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_profiles import apply_user_config_profiles
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.settings import PLUGIN_CONFIG_ROOTS, PLUGIN_SHUTDOWN_TIMEOUT
from plugin.utils import parse_bool_config

logger = get_logger("server.application.plugins.lifecycle")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@runtime_checkable
class PluginHostContract(Protocol):
    async def start(self, message_target_queue: object) -> None: ...

    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None: ...

    async def send_extension_command(
        self,
        msg_type: str,
        payload: dict[str, object],
        timeout: float = 10.0,
    ) -> object: ...

    def is_alive(self) -> bool: ...


@dataclass(slots=True, frozen=True)
class _ReloadOutcome:
    plugin_id: str
    success: bool
    error: str | None = None


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _detail_to_message(detail: object, *, default_message: str) -> str:
    if isinstance(detail, str) and detail:
        return detail
    return default_message


def _to_domain_error(
    *,
    code: str,
    message: str,
    status_code: int,
    plugin_id: str | None,
    error_type: str,
) -> ServerDomainError:
    return ServerDomainError(
        code=code,
        message=message,
        status_code=status_code,
        details={
            "plugin_id": plugin_id or "",
            "error_type": error_type,
        },
    )


def _get_plugin_host_sync(plugin_id: str) -> object | None:
    with state.acquire_plugin_hosts_read_lock():
        return state.plugin_hosts.get(plugin_id)


def _pop_plugin_host_sync(plugin_id: str) -> object | None:
    with state.acquire_plugin_hosts_write_lock():
        popped = state.plugin_hosts.pop(plugin_id, None)
    if popped is not None:
        state.invalidate_snapshot_cache("hosts")
    return popped


def _plugin_is_running_sync(plugin_id: str) -> bool:
    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


def _list_running_plugin_ids_sync() -> list[str]:
    with state.acquire_plugin_hosts_read_lock():
        return [plugin_id for plugin_id in state.plugin_hosts.keys()]


def _remove_event_handlers_sync(plugin_id: str) -> None:
    removed_any = False
    with state.acquire_event_handlers_write_lock():
        target_prefix_dot = f"{plugin_id}."
        target_prefix_colon = f"{plugin_id}:"
        keys_to_remove = [
            key
            for key in list(state.event_handlers.keys())
            if key.startswith(target_prefix_dot) or key.startswith(target_prefix_colon)
        ]
        for key in keys_to_remove:
            del state.event_handlers[key]
            removed_any = True
    if removed_any:
        state.invalidate_snapshot_cache("handlers")


def _get_plugin_meta_sync(plugin_id: str) -> dict[str, object] | None:
    with state.acquire_plugins_read_lock():
        raw_meta = state.plugins.get(plugin_id)
    if not isinstance(raw_meta, dict):
        return None

    normalized: dict[str, object] = {}
    for key, value in raw_meta.items():
        if isinstance(key, str):
            normalized[key] = value
    return normalized


def _set_plugin_runtime_enabled_sync(plugin_id: str, enabled: bool) -> None:
    with state.acquire_plugins_write_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict):
            return
        raw_meta["runtime_enabled"] = enabled
        state.plugins[plugin_id] = raw_meta
    state.invalidate_snapshot_cache("plugins")


def _set_plugin_runtime_metadata_sync(
    plugin_id: str,
    *,
    runtime_enabled: bool,
    runtime_auto_start: bool,
    entries_preview: list[dict[str, object]] | None = None,
) -> None:
    with state.acquire_plugins_write_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict):
            return
        raw_meta["runtime_enabled"] = runtime_enabled
        raw_meta["runtime_auto_start"] = runtime_auto_start
        if entries_preview is not None:
            raw_meta["entries_preview"] = entries_preview
        state.plugins[plugin_id] = raw_meta
    state.invalidate_snapshot_cache("plugins")


def _get_plugin_config_path(plugin_id: str) -> Path | None:
    normalized_plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
        return None

    for root in PLUGIN_CONFIG_ROOTS:
        resolved_root = root.resolve()
        config_file = (resolved_root / normalized_plugin_id / "plugin.toml").resolve()
        if resolved_root not in config_file.parents:
            continue
        if config_file.exists():
            return config_file
    return None


def _register_or_replace_host_sync(plugin_id: str, host: PluginHostContract) -> int:
    with state.acquire_plugin_hosts_write_lock():
        if plugin_id in state.plugin_hosts:
            existing_host = state.plugin_hosts.get(plugin_id)
            if existing_host is not None and existing_host is not host:
                logger.warning("Plugin {} already exists in plugin_hosts, replacing host", plugin_id)
        state.plugin_hosts[plugin_id] = host
        current_count = len(state.plugin_hosts)
    state.invalidate_snapshot_cache("hosts")
    return current_count


def _remap_entries_preview_plugin_id(
    entries_preview: list[dict[str, object]],
    *,
    plugin_id: str,
) -> list[dict[str, object]]:
    remapped: list[dict[str, object]] = []
    for item in entries_preview:
        entry_copy = dict(item)
        entry_id_obj = entry_copy.get("id")
        if isinstance(entry_id_obj, str) and entry_id_obj:
            entry_copy["event_key"] = f"{plugin_id}.{entry_id_obj}"
        remapped.append(entry_copy)
    return remapped


def _read_plugin_config_sync(config_path: Path) -> dict[str, object]:
    with config_path.open("rb") as file_obj:
        raw_conf = tomllib.load(file_obj)
    if not isinstance(raw_conf, Mapping):
        raise ValueError("plugin config root must be an object")
    return _normalize_mapping(raw_conf, context=f"plugin_config[{config_path}]")


def _resolve_plugin_author(pdata: dict[str, object]) -> PluginAuthor | None:
    raw_author = pdata.get("author")
    if not isinstance(raw_author, Mapping):
        return None

    author_mapping = _normalize_mapping(raw_author, context="plugin.author")
    raw_name = author_mapping.get("name")
    raw_email = author_mapping.get("email")
    name = str(raw_name) if isinstance(raw_name, str) else None
    email = str(raw_email) if isinstance(raw_email, str) else None
    return PluginAuthor(name=name, email=email)


def _resolve_plugin_meta_type(raw_type: object) -> str:
    if raw_type in ("plugin", "adapter", "script"):
        return str(raw_type)
    return "plugin"


async def _cleanup_started_host(plugin_id: str, host: PluginHostContract) -> None:
    removed = await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
    target_host = host
    if isinstance(removed, PluginHostContract):
        target_host = removed

    try:
        await target_host.shutdown(timeout=1.0)
    except PluginError as exc:
        logger.warning(
            "cleanup shutdown failed with PluginError: plugin_id={}, err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )
    except RUNTIME_ERRORS as exc:
        logger.warning(
            "cleanup shutdown failed: plugin_id={}, err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )


def _read_extension_prefix_sync(config_path: Path) -> str:
    with config_path.open("rb") as file_obj:
        raw_conf = tomllib.load(file_obj)

    plugin_conf_obj = raw_conf.get("plugin")
    if not isinstance(plugin_conf_obj, Mapping):
        return ""

    host_conf_obj = plugin_conf_obj.get("host")
    if not isinstance(host_conf_obj, Mapping):
        return ""

    prefix_obj = host_conf_obj.get("prefix")
    if isinstance(prefix_obj, str):
        return prefix_obj
    return ""


def _emit_lifecycle_event(
    *,
    event_type: str,
    plugin_id: str | None = None,
    host_plugin_id: str | None = None,
    data: Mapping[str, object] | None = None,
) -> None:
    event: dict[str, object] = {
        "type": event_type,
    }
    if plugin_id is not None:
        event["plugin_id"] = plugin_id
    if host_plugin_id is not None:
        event["host_plugin_id"] = host_plugin_id
    if data is not None:
        event["data"] = dict(data)
    emit_lifecycle_event(event)


class PluginLifecycleService:
    async def start_plugin(self, plugin_id: str, restore_state: bool = False) -> dict[str, object]:
        start_time = time_module.perf_counter()
        original_plugin_id = plugin_id

        existing_host_obj = await asyncio.to_thread(_get_plugin_host_sync, plugin_id)
        if isinstance(existing_host_obj, PluginHostContract):
            if existing_host_obj.is_alive():
                _emit_lifecycle_event(event_type="plugin_start_skipped", plugin_id=plugin_id)
                return {
                    "success": True,
                    "plugin_id": plugin_id,
                    "message": "Plugin is already running",
                }
            # Stale host (process dead) — remove so re-start can proceed
            await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
            logger.info("removed stale host for plugin_id={} (process no longer alive)", plugin_id)

        if state.is_plugin_frozen(plugin_id) and not restore_state:
            raise _to_domain_error(
                code="PLUGIN_FROZEN",
                message=f"Plugin '{plugin_id}' is frozen. Use unfreeze_plugin to restore it.",
                status_code=409,
                plugin_id=plugin_id,
                error_type="PluginFrozen",
            )

        config_path = _get_plugin_config_path(plugin_id)
        if config_path is None:
            raise _to_domain_error(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{plugin_id}' configuration not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="ConfigNotFound",
            )

        host_obj: PluginHostContract | None = None
        registered_plugin_id: str | None = None
        current_plugin_id = plugin_id

        try:
            conf = await asyncio.to_thread(_read_plugin_config_sync, config_path)
            logger.info(
                "start_plugin config loaded: plugin_id={}, elapsed={:.3f}s",
                current_plugin_id,
                time_module.perf_counter() - start_time,
            )

            try:
                conf = await asyncio.to_thread(
                    apply_user_config_profiles,
                    plugin_id=str(current_plugin_id),
                    base_config=conf,
                    config_path=config_path,
                )
            except HTTPException as exc:
                raise _to_domain_error(
                    code="PLUGIN_CONFIG_PROFILE_FAILED",
                    message=_detail_to_message(exc.detail, default_message="Failed to apply user config profiles"),
                    status_code=exc.status_code,
                    plugin_id=current_plugin_id,
                    error_type="HTTPException",
                ) from exc
            except IO_RUNTIME_ERRORS as exc:
                logger.warning(
                    "apply user config profiles failed: plugin_id={}, err_type={}, err={}",
                    current_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
            if not isinstance(conf, Mapping):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CONFIG",
                    message=f"Plugin '{current_plugin_id}' config is invalid after profile overlay",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type="InvalidConfigAfterProfile",
                )
            conf = _normalize_mapping(conf, context=f"plugin_config[{current_plugin_id}]")

            plugin_obj = conf.get("plugin")
            if not isinstance(plugin_obj, Mapping):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CONFIG",
                    message=f"Plugin '{current_plugin_id}' has invalid [plugin] section",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="InvalidPluginSection",
                )
            pdata = _normalize_mapping(plugin_obj, context=f"plugin_config[{current_plugin_id}].plugin")

            runtime_obj = conf.get("plugin_runtime")
            enabled_value = True
            auto_start_value = True
            if isinstance(runtime_obj, Mapping):
                runtime_cfg = _normalize_mapping(runtime_obj, context=f"plugin_config[{current_plugin_id}].plugin_runtime")
                enabled_value = parse_bool_config(runtime_cfg.get("enabled"), default=True)
                auto_start_value = parse_bool_config(runtime_cfg.get("auto_start"), default=True)
            if not enabled_value:
                raise _to_domain_error(
                    code="PLUGIN_DISABLED",
                    message=f"Plugin '{current_plugin_id}' is disabled by plugin_runtime.enabled and cannot be started",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="PluginDisabled",
                )

            plugin_type_obj = pdata.get("type")
            if plugin_type_obj == "extension":
                host_pid = "unknown"
                host_obj_cfg = pdata.get("host")
                if isinstance(host_obj_cfg, Mapping):
                    host_cfg = _normalize_mapping(host_obj_cfg, context=f"plugin_config[{current_plugin_id}].plugin.host")
                    host_pid_obj = host_cfg.get("plugin_id")
                    if isinstance(host_pid_obj, str) and host_pid_obj:
                        host_pid = host_pid_obj
                raise _to_domain_error(
                    code="EXTENSION_CANNOT_START_INDEPENDENT",
                    message=(
                        f"Plugin '{current_plugin_id}' is an extension (type='extension') and cannot be started "
                        f"as an independent process. It will be automatically injected into its host plugin "
                        f"'{host_pid}' when the host starts."
                    ),
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="ExtensionCannotStart",
                )

            entry_obj = pdata.get("entry")
            if not isinstance(entry_obj, str) or ":" not in entry_obj:
                raise _to_domain_error(
                    code="INVALID_PLUGIN_ENTRY",
                    message=f"Invalid entry point for plugin '{current_plugin_id}'",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="InvalidEntryPoint",
                )
            entry = entry_obj

            resolved_id = _resolve_plugin_id_conflict(
                current_plugin_id,
                logger,
                config_path=config_path,
                entry_point=entry,
                plugin_data=pdata,
                purpose="load",
            )
            if resolved_id is None:
                raise _to_domain_error(
                    code="PLUGIN_ALREADY_LOADED",
                    message=f"Plugin '{current_plugin_id}' is already loaded (duplicate detected)",
                    status_code=409,
                    plugin_id=current_plugin_id,
                    error_type="DuplicatePlugin",
                )
            current_plugin_id = resolved_id

            _emit_lifecycle_event(event_type="plugin_start_requested", plugin_id=current_plugin_id)
            created_host = await asyncio.to_thread(
                PluginProcessHost,
                plugin_id=current_plugin_id,
                entry_point=entry,
                config_path=config_path,
            )
            if not isinstance(created_host, PluginHostContract):
                raise _to_domain_error(
                    code="INVALID_HOST_OBJECT",
                    message=f"Plugin '{current_plugin_id}' host object is invalid",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type=type(created_host).__name__,
                )
            host_obj = created_host

            author = _resolve_plugin_author(pdata)
            dependencies = _parse_plugin_dependencies(conf, logger, current_plugin_id)
            for dep in dependencies:
                satisfied, error_message = _check_plugin_dependency(dep, logger, current_plugin_id)
                if not satisfied:
                    raise _to_domain_error(
                        code="PLUGIN_DEPENDENCY_CHECK_FAILED",
                        message=f"Plugin dependency check failed for plugin '{current_plugin_id}': {error_message}",
                        status_code=400,
                        plugin_id=current_plugin_id,
                        error_type="DependencyCheckFailed",
                    )

            await host_obj.start(message_target_queue=state.message_queue)

            process_obj = getattr(created_host, "process", None)
            if process_obj is not None and hasattr(process_obj, "is_alive"):
                if not process_obj.is_alive():
                    exitcode_obj = getattr(process_obj, "exitcode", None)
                    exitcode_text = str(exitcode_obj) if exitcode_obj is not None else "unknown"
                    raise _to_domain_error(
                        code="PLUGIN_PROCESS_DIED_IMMEDIATELY",
                        message=(
                            f"Plugin '{current_plugin_id}' process died immediately after startup "
                            f"(exitcode: {exitcode_text})"
                        ),
                        status_code=500,
                        plugin_id=current_plugin_id,
                        error_type="ProcessDiedImmediately",
                    )

            module_path, class_name = entry.split(":", 1)
            module_obj = await asyncio.to_thread(importlib.import_module, module_path)
            cls_obj = getattr(module_obj, class_name)
            if not isinstance(cls_obj, type):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CLASS",
                    message=f"Plugin '{current_plugin_id}' entry class '{class_name}' is invalid",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type="InvalidPluginClass",
                )

            await asyncio.to_thread(scan_static_metadata, current_plugin_id, cls_obj, conf, pdata)
            entries_preview = await asyncio.to_thread(
                _extract_entries_preview,
                current_plugin_id,
                cls_obj,
                conf,
                pdata,
            )

            plugin_meta = PluginMeta(
                id=current_plugin_id,
                name=str(pdata.get("name")) if isinstance(pdata.get("name"), str) else current_plugin_id,
                type=_resolve_plugin_meta_type(plugin_type_obj),
                description=str(pdata.get("description")) if isinstance(pdata.get("description"), str) else "",
                version=str(pdata.get("version")) if isinstance(pdata.get("version"), str) else "0.1.0",
                sdk_version=SDK_VERSION,
                author=author,
                dependencies=dependencies,
            )

            final_plugin_id = register_plugin(
                plugin_meta,
                logger,
                config_path=config_path,
                entry_point=entry,
            )
            if final_plugin_id is None:
                raise _to_domain_error(
                    code="PLUGIN_ALREADY_REGISTERED",
                    message=f"Plugin '{current_plugin_id}' is already registered (duplicate detected)",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="DuplicateRegister",
                )

            if final_plugin_id != current_plugin_id and hasattr(created_host, "plugin_id"):
                setattr(created_host, "plugin_id", final_plugin_id)
            if final_plugin_id != current_plugin_id:
                entries_preview = _remap_entries_preview_plugin_id(
                    entries_preview,
                    plugin_id=final_plugin_id,
                )
            current_plugin_id = final_plugin_id
            await asyncio.to_thread(
                _set_plugin_runtime_metadata_sync,
                current_plugin_id,
                runtime_enabled=True,
                runtime_auto_start=auto_start_value,
                entries_preview=entries_preview,
            )

            await asyncio.to_thread(_register_or_replace_host_sync, current_plugin_id, host_obj)
            registered_plugin_id = current_plugin_id

            _emit_lifecycle_event(event_type="plugin_started", plugin_id=current_plugin_id)
            response: dict[str, object] = {
                "success": True,
                "plugin_id": current_plugin_id,
                "message": "Plugin started successfully",
            }
            if current_plugin_id != original_plugin_id:
                response["original_plugin_id"] = original_plugin_id
                response["message"] = (
                    f"Plugin started successfully (renamed from '{original_plugin_id}' to "
                    f"'{current_plugin_id}' due to ID conflict)"
                )
            return response
        except ServerDomainError:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise
        except HTTPException as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message=_detail_to_message(exc.detail, default_message="start_plugin failed"),
                status_code=exc.status_code,
                plugin_id=current_plugin_id,
                error_type="HTTPException",
            ) from exc
        except PluginError as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message=str(exc),
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except (ImportError, ModuleNotFoundError) as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_IMPORT_FAILED",
                message=f"Failed to import plugin '{current_plugin_id}' module",
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except RUNTIME_ERRORS as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message="start_plugin failed",
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc

    async def stop_plugin(self, plugin_id: str) -> dict[str, object]:
        host_obj = await asyncio.to_thread(_get_plugin_host_sync, plugin_id)
        if host_obj is None:
            raise _to_domain_error(
                code="PLUGIN_NOT_RUNNING",
                message=f"Plugin '{plugin_id}' is not running",
                status_code=404,
                plugin_id=plugin_id,
                error_type="PluginNotRunning",
            )

        if not isinstance(host_obj, PluginHostContract):
            raise _to_domain_error(
                code="INVALID_HOST_OBJECT",
                message=f"Plugin '{plugin_id}' host object is invalid",
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(host_obj).__name__,
            )

        try:
            _emit_lifecycle_event(event_type="plugin_stop_requested", plugin_id=plugin_id)
            await host_obj.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
            await asyncio.to_thread(_remove_event_handlers_sync, plugin_id)
            _emit_lifecycle_event(event_type="plugin_stopped", plugin_id=plugin_id)
            return {
                "success": True,
                "plugin_id": plugin_id,
                "message": "Plugin stopped successfully",
            }
        except PluginError as exc:
            logger.error(
                "stop_plugin failed with PluginError: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="PLUGIN_STOP_FAILED",
                message=str(exc),
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "stop_plugin failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="PLUGIN_STOP_FAILED",
                message="stop_plugin failed",
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc

    async def reload_plugin(self, plugin_id: str) -> dict[str, object]:
        _emit_lifecycle_event(event_type="plugin_reload_requested", plugin_id=plugin_id)

        is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
        if is_running:
            try:
                await self.stop_plugin(plugin_id)
            except ServerDomainError as error:
                if error.status_code != 404:
                    raise

        result = await self.start_plugin(plugin_id)
        _emit_lifecycle_event(event_type="plugin_reloaded", plugin_id=plugin_id)
        return result

    async def reload_all_plugins(self) -> dict[str, object]:
        start_time = time_module.perf_counter()
        _emit_lifecycle_event(event_type="plugins_reload_all_requested")

        running_plugin_ids = await asyncio.to_thread(_list_running_plugin_ids_sync)
        if not running_plugin_ids:
            return {
                "success": True,
                "reloaded": [],
                "failed": [],
                "skipped": [],
                "message": "No running plugins to reload",
            }

        stop_tasks = [self._safe_stop_for_reload(plugin_id) for plugin_id in running_plugin_ids]
        stop_outcomes = await asyncio.gather(*stop_tasks)

        plugins_to_start: list[str] = []
        failed: list[dict[str, object]] = []
        for outcome in stop_outcomes:
            if outcome.success:
                plugins_to_start.append(outcome.plugin_id)
                continue
            failed.append({"plugin_id": outcome.plugin_id, "error": outcome.error or "Stop failed"})

        start_tasks = [self._safe_start_for_reload(plugin_id) for plugin_id in plugins_to_start]
        start_outcomes = await asyncio.gather(*start_tasks)

        reloaded: list[str] = []
        for outcome in start_outcomes:
            if outcome.success:
                reloaded.append(outcome.plugin_id)
                continue
            failed.append({"plugin_id": outcome.plugin_id, "error": outcome.error or "Start failed"})

        elapsed = time_module.perf_counter() - start_time
        success = len(failed) == 0
        message: str
        if success:
            message = f"Successfully reloaded {len(reloaded)} plugins (took {elapsed:.3f}s)"
        else:
            message = f"Reloaded {len(reloaded)} plugins, {len(failed)} failed (took {elapsed:.3f}s)"

        _emit_lifecycle_event(
            event_type="plugins_reload_all_completed",
            data={
                "reloaded_count": len(reloaded),
                "failed_count": len(failed),
                "duration_seconds": round(elapsed, 3),
            },
        )

        return {
            "success": success,
            "reloaded": reloaded,
            "failed": failed,
            "skipped": [],
            "message": message,
        }

    async def disable_extension(self, ext_id: str) -> dict[str, object]:
        _ext_meta, host_plugin_id, host_obj = await self._validate_extension(ext_id)

        result: dict[str, object] = {
            "success": False,
            "ext_id": ext_id,
            "host_plugin_id": host_plugin_id,
        }

        if host_obj is not None and host_obj.is_alive():
            try:
                response_data = await host_obj.send_extension_command(
                    "DISABLE_EXTENSION",
                    {"ext_name": ext_id},
                    timeout=10.0,
                )
            except PluginError as exc:
                logger.error(
                    "disable_extension host command failed with PluginError: ext_id={}, host_plugin_id={}, err_type={}, err={}",
                    ext_id,
                    host_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
                raise _to_domain_error(
                    code="EXTENSION_DISABLE_FAILED",
                    message=str(exc),
                    status_code=500,
                    plugin_id=ext_id,
                    error_type=type(exc).__name__,
                ) from exc
            except RUNTIME_ERRORS as exc:
                logger.error(
                    "disable_extension host command failed: ext_id={}, host_plugin_id={}, err_type={}, err={}",
                    ext_id,
                    host_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
                raise _to_domain_error(
                    code="EXTENSION_DISABLE_FAILED",
                    message="disable_extension failed",
                    status_code=500,
                    plugin_id=ext_id,
                    error_type=type(exc).__name__,
                ) from exc

            result["success"] = True
            result["data"] = response_data
        else:
            result["success"] = True
            result["message"] = "Host not running; extension metadata updated"

        await asyncio.to_thread(_set_plugin_runtime_enabled_sync, ext_id, False)
        _emit_lifecycle_event(
            event_type="extension_disabled",
            plugin_id=ext_id,
            host_plugin_id=host_plugin_id,
        )
        return result

    async def enable_extension(self, ext_id: str) -> dict[str, object]:
        ext_meta, host_plugin_id, host_obj = await self._validate_extension(ext_id)

        ext_entry_obj = ext_meta.get("entry_point")
        if not isinstance(ext_entry_obj, str) or not ext_entry_obj:
            raise _to_domain_error(
                code="INVALID_EXTENSION_METADATA",
                message=f"Extension '{ext_id}' has invalid entry_point",
                status_code=500,
                plugin_id=ext_id,
                error_type="InvalidEntryPoint",
            )

        prefix = ""
        config_path_obj = ext_meta.get("config_path")
        if isinstance(config_path_obj, str) and config_path_obj:
            config_path = Path(config_path_obj)
            try:
                prefix = await asyncio.to_thread(_read_extension_prefix_sync, config_path)
            except (FileNotFoundError, PermissionError, OSError, ValueError) as exc:
                logger.warning(
                    "failed to read extension prefix: ext_id={}, config_path={}, err_type={}, err={}",
                    ext_id,
                    str(config_path),
                    type(exc).__name__,
                    str(exc),
                )

        result: dict[str, object] = {
            "success": False,
            "ext_id": ext_id,
            "host_plugin_id": host_plugin_id,
        }

        if host_obj is not None and host_obj.is_alive():
            try:
                response_data = await host_obj.send_extension_command(
                    "ENABLE_EXTENSION",
                    {
                        "ext_id": ext_id,
                        "ext_entry": ext_entry_obj,
                        "prefix": prefix,
                    },
                    timeout=10.0,
                )
            except PluginError as exc:
                logger.error(
                    "enable_extension host command failed with PluginError: ext_id={}, host_plugin_id={}, err_type={}, err={}",
                    ext_id,
                    host_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
                raise _to_domain_error(
                    code="EXTENSION_ENABLE_FAILED",
                    message=str(exc),
                    status_code=500,
                    plugin_id=ext_id,
                    error_type=type(exc).__name__,
                ) from exc
            except RUNTIME_ERRORS as exc:
                logger.error(
                    "enable_extension host command failed: ext_id={}, host_plugin_id={}, err_type={}, err={}",
                    ext_id,
                    host_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
                raise _to_domain_error(
                    code="EXTENSION_ENABLE_FAILED",
                    message="enable_extension failed",
                    status_code=500,
                    plugin_id=ext_id,
                    error_type=type(exc).__name__,
                ) from exc

            result["success"] = True
            result["data"] = response_data
        else:
            result["success"] = True
            result["message"] = "Host not running; extension will be injected when host starts"

        await asyncio.to_thread(_set_plugin_runtime_enabled_sync, ext_id, True)
        _emit_lifecycle_event(
            event_type="extension_enabled",
            plugin_id=ext_id,
            host_plugin_id=host_plugin_id,
        )
        return result

    async def _safe_stop_for_reload(self, plugin_id: str) -> _ReloadOutcome:
        try:
            await self.stop_plugin(plugin_id)
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except ServerDomainError as error:
            if error.status_code == 404:
                return _ReloadOutcome(plugin_id=plugin_id, success=True)
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)

    async def _safe_start_for_reload(self, plugin_id: str) -> _ReloadOutcome:
        try:
            await self.start_plugin(plugin_id)
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except ServerDomainError as error:
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)

    async def _validate_extension(self, ext_id: str) -> tuple[dict[str, object], str, PluginHostContract | None]:
        ext_meta = await asyncio.to_thread(_get_plugin_meta_sync, ext_id)
        if ext_meta is None:
            raise _to_domain_error(
                code="EXTENSION_NOT_FOUND",
                message=f"Extension '{ext_id}' not found",
                status_code=404,
                plugin_id=ext_id,
                error_type="ExtensionNotFound",
            )

        plugin_type_obj = ext_meta.get("type")
        if plugin_type_obj != "extension":
            raise _to_domain_error(
                code="INVALID_EXTENSION_TYPE",
                message=f"'{ext_id}' is not an extension plugin",
                status_code=400,
                plugin_id=ext_id,
                error_type="InvalidExtensionType",
            )

        host_plugin_id_obj = ext_meta.get("host_plugin_id")
        if not isinstance(host_plugin_id_obj, str) or not host_plugin_id_obj:
            raise _to_domain_error(
                code="INVALID_EXTENSION_METADATA",
                message=f"Extension '{ext_id}' has no host_plugin_id",
                status_code=400,
                plugin_id=ext_id,
                error_type="MissingHostPluginId",
            )

        host_obj_raw = await asyncio.to_thread(_get_plugin_host_sync, host_plugin_id_obj)
        if host_obj_raw is None:
            return ext_meta, host_plugin_id_obj, None

        if not isinstance(host_obj_raw, PluginHostContract):
            raise _to_domain_error(
                code="INVALID_HOST_OBJECT",
                message=f"Host plugin '{host_plugin_id_obj}' object is invalid",
                status_code=500,
                plugin_id=host_plugin_id_obj,
                error_type=type(host_obj_raw).__name__,
            )

        return ext_meta, host_plugin_id_obj, host_obj_raw

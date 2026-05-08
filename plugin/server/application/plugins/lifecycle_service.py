from __future__ import annotations

import asyncio
import importlib
import re
import shutil
import time as time_module
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastapi import HTTPException

from plugin._types.exceptions import PluginError
from plugin.core.host import PluginProcessHost
from plugin.core.registry import (
    _collect_plugin_python_requirements,
    _check_plugin_dependency,
    _extract_entries_preview,
    _find_missing_python_requirements,
    _parse_plugin_dependencies,
    _resolve_plugin_id_conflict,
    scan_static_metadata,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS, RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.runtime_overrides import (
    clear_runtime_override,
    set_runtime_override,
)
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.llm_tool_registry import (
    clear_plugin_tools as clear_plugin_llm_tools,
)
from plugin.settings import PLUGIN_CONFIG_ROOTS, PLUGIN_SHUTDOWN_TIMEOUT
from plugin.utils import parse_bool_config

logger = get_logger("server.application.plugins.lifecycle")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
plugin_registry_service = PluginRegistryService()


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
        raw_meta.pop("runtime_load_state", None)
        raw_meta.pop("runtime_load_error_type", None)
        raw_meta.pop("runtime_load_error_message", None)
        raw_meta.pop("runtime_load_error_phase", None)
        raw_meta.pop("runtime_load_error_time", None)
        raw_meta.pop("runtime_source_missing", None)
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


def _resolve_plugin_dir_sync(plugin_id: str, plugin_meta: dict[str, object] | None) -> Path | None:
    config_path = _resolve_registered_config_path_sync(plugin_meta)
    if config_path is None:
        config_path = _get_plugin_config_path(plugin_id)
    if config_path is None:
        return None
    try:
        return config_path.parent.resolve()
    except Exception:
        return config_path.parent


def _path_within_plugin_roots_sync(path: Path) -> bool:
    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path

    for root in PLUGIN_CONFIG_ROOTS:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root
        if resolved_path == resolved_root or resolved_root in resolved_path.parents:
            return True
    return False


def _list_bound_extensions_sync(host_plugin_id: str) -> list[str]:
    bound_extensions: list[str] = []
    with state.acquire_plugins_read_lock():
        snapshot = {
            plugin_id: dict(meta)
            for plugin_id, meta in state.plugins.items()
            if isinstance(plugin_id, str) and isinstance(meta, dict)
        }

    for plugin_id, meta in snapshot.items():
        if meta.get("type") != "extension":
            continue
        if meta.get("host_plugin_id") != host_plugin_id:
            continue
        if meta.get("runtime_source_missing") is True:
            continue
        bound_extensions.append(plugin_id)

    bound_extensions.sort()
    return bound_extensions


def _remove_plugin_metadata_sync(plugin_id: str) -> bool:
    removed = False
    with state.acquire_plugins_write_lock():
        if plugin_id in state.plugins:
            state.plugins.pop(plugin_id, None)
            removed = True
    if removed:
        state.invalidate_snapshot_cache("plugins")
    return removed


def _delete_plugin_directory_sync(plugin_dir: Path) -> bool:
    if not plugin_dir.exists():
        return False
    shutil.rmtree(plugin_dir)
    return True


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


def _read_plugin_config_sync(config_path: Path) -> dict[str, object]:
    with config_path.open("rb") as file_obj:
        raw_conf = tomllib.load(file_obj)
    if not isinstance(raw_conf, Mapping):
        raise ValueError("plugin config root must be an object")
    return _normalize_mapping(raw_conf, context=f"plugin_config[{config_path}]")


def _resolve_registered_config_path_sync(plugin_meta: dict[str, object] | None) -> Path | None:
    if not isinstance(plugin_meta, dict):
        return None

    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None

    try:
        return Path(config_path_obj).resolve()
    except Exception:
        return Path(config_path_obj)


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
    async def start_plugin(
        self,
        plugin_id: str,
        restore_state: bool = False,
        *,
        refresh_registry: bool = True,
    ) -> dict[str, object]:
        start_time = time_module.perf_counter()
        original_plugin_id = plugin_id
        current_plugin_id = plugin_id

        existing_host_obj = await asyncio.to_thread(_get_plugin_host_sync, current_plugin_id)
        if isinstance(existing_host_obj, PluginHostContract):
            if existing_host_obj.is_alive():
                _emit_lifecycle_event(event_type="plugin_start_skipped", plugin_id=current_plugin_id)
                return {
                    "success": True,
                    "plugin_id": current_plugin_id,
                    "message": "Plugin is already running",
                }
            # Stale host (process dead) — remove so re-start can proceed
            await asyncio.to_thread(_pop_plugin_host_sync, current_plugin_id)
            logger.info("removed stale host for plugin_id={} (process no longer alive)", current_plugin_id)

        if state.is_plugin_frozen(current_plugin_id) and not restore_state:
            raise _to_domain_error(
                code="PLUGIN_FROZEN",
                message=f"Plugin '{current_plugin_id}' is frozen. Use unfreeze_plugin to restore it.",
                status_code=409,
                plugin_id=current_plugin_id,
                error_type="PluginFrozen",
            )

        if refresh_registry:
            try:
                refresh_payload = await plugin_registry_service.refresh_plugin(current_plugin_id)
                refreshed_plugin_id = refresh_payload.get("plugin_id")
                if isinstance(refreshed_plugin_id, str) and refreshed_plugin_id:
                    current_plugin_id = refreshed_plugin_id
            except ServerDomainError as exc:
                if exc.code == "PLUGIN_CONFIG_NOT_FOUND":
                    logger.warning(
                        "registry refresh skipped for plugin_id={} because config lookup disagreed with lifecycle path resolution",
                        current_plugin_id,
                    )
                else:
                    raise _to_domain_error(
                        code=exc.code,
                        message=exc.message,
                        status_code=exc.status_code,
                        plugin_id=current_plugin_id,
                        error_type=str(exc.details.get("error_type", "RegistryRefreshFailed")) if isinstance(exc.details, dict) else "RegistryRefreshFailed",
                    ) from exc

        registered_meta = await asyncio.to_thread(_get_plugin_meta_sync, current_plugin_id)
        config_path = await asyncio.to_thread(_resolve_registered_config_path_sync, registered_meta)
        if config_path is None:
            config_path = _get_plugin_config_path(current_plugin_id)
        if config_path is None:
            raise _to_domain_error(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{current_plugin_id}' configuration not found",
                status_code=404,
                plugin_id=current_plugin_id,
                error_type="ConfigNotFound",
            )

        host_obj: PluginHostContract | None = None
        registered_plugin_id: str | None = None

        try:
            conf = await asyncio.to_thread(_read_plugin_config_sync, config_path)
            logger.info(
                "start_plugin config loaded: plugin_id={}, elapsed={:.3f}s",
                current_plugin_id,
                time_module.perf_counter() - start_time,
            )

            try:
                resolved_conf = await asyncio.to_thread(
                    resolve_plugin_config_from_path,
                    str(current_plugin_id),
                    config_path=config_path,
                    base_config=conf,
                    include_effective_config=True,
                    validate_schema=True,
                )
                warnings_obj = resolved_conf.get("warnings")
                if isinstance(warnings_obj, list):
                    for warning in warnings_obj:
                        if isinstance(warning, Mapping):
                            logger.warning(
                                "Plugin config warning [{}] field={} msg={}",
                                warning.get("code"),
                                warning.get("field"),
                                warning.get("message"),
                            )
                conf = resolved_conf.get("effective_config")
            except HTTPException as exc:
                raise _to_domain_error(
                    code="PLUGIN_CONFIG_PROFILE_FAILED",
                    message=_detail_to_message(exc.detail, default_message="Failed to resolve plugin config"),
                    status_code=exc.status_code,
                    plugin_id=current_plugin_id,
                    error_type="HTTPException",
                ) from exc
            except IO_RUNTIME_ERRORS as exc:
                logger.warning(
                    "resolve plugin config failed: plugin_id={}, err_type={}, err={}",
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
            python_requirements = _collect_plugin_python_requirements(
                conf,
                config_path,
                logger,
                current_plugin_id,
            )
            unsatisfied_python_requirements = _find_missing_python_requirements(python_requirements)
            if unsatisfied_python_requirements:
                raise _to_domain_error(
                    code="PLUGIN_PYTHON_DEPENDENCIES_MISSING",
                    message=(
                        f"Plugin '{current_plugin_id}' has unsatisfied Python dependencies: "
                        f"{unsatisfied_python_requirements}. Install compatible packages in the current runtime environment."
                    ),
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="MissingPythonDependencies",
                )

            _emit_lifecycle_event(event_type="plugin_start_requested", plugin_id=current_plugin_id)
            extension_configs = await plugin_registry_service.list_extension_configs_for_host(current_plugin_id)
            created_host = await asyncio.to_thread(
                PluginProcessHost,
                plugin_id=current_plugin_id,
                entry_point=entry,
                config_path=config_path,
                extension_configs=extension_configs or None,
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
            # Clear any LLM tools the plugin had registered with
            # ``main_server``. Best-effort: a transient HTTP failure
            # here shouldn't block the rest of plugin teardown — the
            # registration helper logs the error itself. Without this
            # call, a stopped plugin's tools would linger in
            # main_server's registry until process restart, and the
            # model could still pick them only to hit a 404 on
            # dispatch.
            try:
                await clear_plugin_llm_tools(plugin_id)
            except Exception as exc:
                logger.debug(
                    "clear_plugin_llm_tools failed (best-effort): plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
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

        try:
            await plugin_registry_service.refresh_registry()
        except ServerDomainError as exc:
            raise _to_domain_error(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                plugin_id=None,
                error_type="RegistryRefreshFailed",
            ) from exc

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

        reloaded: list[str] = []
        ordered_plugin_ids = await plugin_registry_service.order_plugin_ids(plugins_to_start)
        for plugin_id in ordered_plugin_ids:
            outcome = await self._safe_start_for_reload(plugin_id)
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

    async def delete_plugin(self, plugin_id: str) -> dict[str, object]:
        plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
        if plugin_meta is None:
            raise _to_domain_error(
                code="PLUGIN_NOT_FOUND",
                message=f"Plugin '{plugin_id}' not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="PluginNotFound",
            )

        plugin_dir = await asyncio.to_thread(_resolve_plugin_dir_sync, plugin_id, plugin_meta)
        if plugin_dir is None:
            raise _to_domain_error(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{plugin_id}' configuration not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="ConfigNotFound",
            )

        path_allowed = await asyncio.to_thread(_path_within_plugin_roots_sync, plugin_dir)
        if not path_allowed:
            raise _to_domain_error(
                code="PLUGIN_DELETE_FORBIDDEN_PATH",
                message=f"Plugin '{plugin_id}' path is outside managed plugin roots",
                status_code=403,
                plugin_id=plugin_id,
                error_type="ForbiddenDeletePath",
            )

        plugin_type = plugin_meta.get("type")
        if plugin_type == "extension":
            ext_meta, host_plugin_id, host_obj = await self._validate_extension(plugin_id)
            runtime_enabled = parse_bool_config(ext_meta.get("runtime_enabled"), default=True)
            if runtime_enabled and host_obj is not None and host_obj.is_alive():
                await self.disable_extension(plugin_id)
        else:
            bound_extensions = await asyncio.to_thread(_list_bound_extensions_sync, plugin_id)
            if bound_extensions:
                raise _to_domain_error(
                    code="PLUGIN_DELETE_BLOCKED_BY_EXTENSIONS",
                    message=(
                        f"Plugin '{plugin_id}' has bound extensions and cannot be deleted yet: "
                        f"{', '.join(bound_extensions)}"
                    ),
                    status_code=409,
                    plugin_id=plugin_id,
                    error_type="BoundExtensionsExist",
                )

            is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
            if is_running:
                await self.stop_plugin(plugin_id)

        try:
            deleted_from_disk = await asyncio.to_thread(_delete_plugin_directory_sync, plugin_dir)
            await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
            await asyncio.to_thread(_remove_event_handlers_sync, plugin_id)
            await asyncio.to_thread(_remove_plugin_metadata_sync, plugin_id)
            await asyncio.to_thread(clear_runtime_override, plugin_id)
            await plugin_registry_service.refresh_registry()
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "delete_plugin failed: plugin_id={}, plugin_dir={}, err_type={}, err={}",
                plugin_id,
                str(plugin_dir),
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="PLUGIN_DELETE_FAILED",
                message=f"Failed to delete plugin '{plugin_id}'",
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc

        _emit_lifecycle_event(
            event_type="plugin_deleted",
            plugin_id=plugin_id,
            data={
                "plugin_dir": str(plugin_dir),
                "deleted_from_disk": deleted_from_disk,
            },
        )
        response: dict[str, object] = {
            "success": True,
            "plugin_id": plugin_id,
            "plugin_dir": str(plugin_dir),
            "deleted_from_disk": deleted_from_disk,
            "message": "Plugin deleted successfully",
        }
        if plugin_type == "extension" and isinstance(host_plugin_id, str) and host_plugin_id:
            response["host_plugin_id"] = host_plugin_id
        return response

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
        await asyncio.to_thread(set_runtime_override, ext_id, False)
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
        await asyncio.to_thread(set_runtime_override, ext_id, True)
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
            await self.start_plugin(plugin_id, refresh_registry=False)
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

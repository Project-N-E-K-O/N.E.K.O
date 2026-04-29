from __future__ import annotations

import asyncio
import importlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugin.core.dependency import _topological_sort_plugins
from plugin.core.registry import (
    PluginContext,
    _build_plugin_meta,
    _check_plugin_dependency,
    _extract_entries_preview,
    _extract_plugin_ui_config,
    _find_missing_python_requirements,
    _parse_single_plugin_config,
    _prepare_plugin_import_roots,
    _resolve_plugin_id_conflict,
    register_plugin,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import PLUGIN_CONFIG_ROOTS

logger = get_logger("server.application.plugins.registry")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_MANAGED_META_KEYS = {
    "id",
    "name",
    "type",
    "plugin_type",
    "description",
    "short_description",
    "keywords",
    "passive",
    "version",
    "sdk_version",
    "sdk_recommended",
    "sdk_supported",
    "sdk_untested",
    "sdk_conflicts",
    "input_schema",
    "author",
    "dependencies",
    "host_plugin_id",
    "i18n",
    "plugin_ui",
    "config_path",
    "entry_point",
    "runtime_enabled",
    "runtime_auto_start",
    "runtime_load_state",
    "runtime_load_error_type",
    "runtime_load_error_message",
    "runtime_load_error_phase",
    "entries_preview",
    "adapter_mode",
    "runtime_source_missing",
}


@dataclass(slots=True)
class PluginDiscoveryRecord:
    plugin_id: str
    original_plugin_id: str
    config_path: Path
    entry_point: str
    plugin_type: str
    enabled: bool
    auto_start: bool
    meta_payload: dict[str, object]


@dataclass(slots=True)
class PluginDiscoveryFailure:
    plugin_id: str | None
    config_path: Path
    error: str


@dataclass(slots=True)
class PluginDiscoverySnapshot:
    records: list[PluginDiscoveryRecord]
    failures: list[PluginDiscoveryFailure]
    config_paths: set[Path]


def _get_registered_plugin_snapshot_sync() -> dict[str, dict[str, object]]:
    with state.acquire_plugins_read_lock():
        snapshot: dict[str, dict[str, object]] = {}
        for plugin_id, meta in state.plugins.items():
            if isinstance(plugin_id, str) and isinstance(meta, dict):
                snapshot[plugin_id] = dict(meta)
        return snapshot


def _list_running_plugin_ids_sync() -> set[str]:
    running: set[str] = set()
    with state.acquire_plugin_hosts_read_lock():
        for plugin_id, host_obj in state.plugin_hosts.items():
            if not isinstance(plugin_id, str):
                continue
            try:
                if hasattr(host_obj, "is_alive") and host_obj.is_alive():
                    running.add(plugin_id)
            except Exception:
                continue
    return running


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


def _select_managed_fields(meta: dict[str, object]) -> dict[str, object]:
    return {
        key: meta[key]
        for key in _MANAGED_META_KEYS
        if key in meta
    }


def _find_plugin_config_path(plugin_id: str, roots: tuple[Path, ...]) -> Path | None:
    normalized_plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
        return None

    for root in roots:
        resolved_root = root.resolve()
        config_file = (resolved_root / normalized_plugin_id / "plugin.toml").resolve()
        if resolved_root not in config_file.parents:
            continue
        if config_file.exists():
            return config_file
    return None


def _resolve_meta_config_path(meta: dict[str, object] | None) -> Path | None:
    if not isinstance(meta, dict):
        return None

    config_path_obj = meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None

    try:
        return Path(config_path_obj).resolve()
    except Exception:
        return Path(config_path_obj)


def _resolve_config_path(path: Path) -> Path:
    try:
        return path.resolve()
    except Exception:
        return path


def _find_existing_runtime_plugin_id_by_config_path(
    config_path: Path,
    existing_snapshot: dict[str, dict[str, object]],
) -> str | None:
    resolved_config_path = _resolve_config_path(config_path)
    for plugin_id, meta in existing_snapshot.items():
        meta_config_path = _resolve_meta_config_path(meta)
        if meta_config_path is not None and meta_config_path == resolved_config_path:
            return plugin_id
    return None


def _read_extension_prefix_sync(config_path: Path) -> str:
    try:
        with config_path.open("rb") as file_obj:
            raw_conf = tomllib.load(file_obj)
    except (FileNotFoundError, PermissionError, OSError, tomllib.TOMLDecodeError):
        return ""

    plugin_conf_obj = raw_conf.get("plugin")
    if not isinstance(plugin_conf_obj, dict):
        return ""

    host_conf_obj = plugin_conf_obj.get("host")
    if not isinstance(host_conf_obj, dict):
        return ""

    prefix_obj = host_conf_obj.get("prefix")
    if isinstance(prefix_obj, str):
        return prefix_obj
    return ""


def _collect_plugin_contexts_from_roots_sync(
    roots: tuple[Path, ...],
) -> tuple[list[PluginContext], dict[str, PluginContext]]:
    plugin_contexts: list[PluginContext] = []
    pid_to_context: dict[str, PluginContext] = {}
    processed_paths: set[Path] = set()

    for root in roots:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root

        if not resolved_root.exists():
            continue

        for config_path in sorted(resolved_root.glob("*/plugin.toml")):
            try:
                ctx = _parse_single_plugin_config(config_path, processed_paths, logger)
            except Exception as exc:
                logger.debug(
                    "plugin context collection skipped failed config {}: err_type={}, err={}",
                    config_path,
                    type(exc).__name__,
                    str(exc),
                )
                continue

            if ctx is None:
                continue
            if ctx.pid in pid_to_context:
                logger.warning(
                    "duplicate plugin id '{}' ignored while building runtime plan",
                    ctx.pid,
                )
                continue

            plugin_contexts.append(ctx)
            pid_to_context[ctx.pid] = ctx

    return plugin_contexts, pid_to_context


def _build_ordered_plugin_ids_sync(candidate_plugin_ids: set[str] | None = None) -> list[str]:
    roots = tuple(PLUGIN_CONFIG_ROOTS)
    plugin_contexts, pid_to_context = _collect_plugin_contexts_from_roots_sync(roots)
    registered_snapshot = _get_registered_plugin_snapshot_sync()
    if not registered_snapshot:
        return []

    target_ids = set(candidate_plugin_ids) if candidate_plugin_ids is not None else set(registered_snapshot.keys())
    if not target_ids:
        return []

    config_path_to_plugin_id: dict[Path, str] = {}
    for plugin_id, meta in registered_snapshot.items():
        resolved_config_path = _resolve_meta_config_path(meta)
        if resolved_config_path is not None:
            config_path_to_plugin_id[resolved_config_path] = plugin_id

    ordered: list[str] = []
    seen: set[str] = set()
    if plugin_contexts:
        for declared_plugin_id in _topological_sort_plugins(plugin_contexts, pid_to_context, logger):
            ctx = pid_to_context.get(declared_plugin_id)
            if ctx is None:
                continue

            try:
                ctx_config_path = ctx.toml_path.resolve()
            except Exception:
                ctx_config_path = ctx.toml_path
            runtime_plugin_id = config_path_to_plugin_id.get(ctx_config_path, declared_plugin_id)
            if runtime_plugin_id not in target_ids or runtime_plugin_id in seen:
                continue
            if runtime_plugin_id not in registered_snapshot:
                continue
            ordered.append(runtime_plugin_id)
            seen.add(runtime_plugin_id)

    for plugin_id in sorted(target_ids):
        if plugin_id in seen or plugin_id not in registered_snapshot:
            continue
        ordered.append(plugin_id)
        seen.add(plugin_id)

    return ordered


def _discover_registry_snapshot_sync(roots: tuple[Path, ...]) -> PluginDiscoverySnapshot:
    processed_paths: set[Path] = set()
    records: list[PluginDiscoveryRecord] = []
    failures: list[PluginDiscoveryFailure] = []
    config_paths: set[Path] = set()

    for root in roots:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root

        if not resolved_root.exists():
            logger.info("No plugin config directory {}, skipping", resolved_root)
            continue

        found_toml_files = sorted(resolved_root.glob("*/plugin.toml"))
        logger.info(
            "Found {} plugin.toml files in {}: {}",
            len(found_toml_files),
            resolved_root,
            [str(path) for path in found_toml_files],
        )

        for config_path in found_toml_files:
            config_paths.add(config_path.resolve())
            try:
                ctx = _parse_single_plugin_config(config_path, processed_paths, logger)
            except Exception as exc:
                logger.warning(
                    "plugin discovery failed for {}: err_type={}, err={}",
                    config_path,
                    type(exc).__name__,
                    str(exc),
                )
                failures.append(
                    PluginDiscoveryFailure(
                        plugin_id=config_path.parent.name or None,
                        config_path=config_path,
                        error=str(exc),
                    )
                )
                continue

            if ctx is None:
                failures.append(
                    PluginDiscoveryFailure(
                        plugin_id=config_path.parent.name or None,
                        config_path=config_path,
                        error="plugin config could not be parsed or validated",
                    )
                )
                continue

            records.append(_build_discovery_record_from_context(ctx))

    return PluginDiscoverySnapshot(
        records=records,
        failures=failures,
        config_paths=config_paths,
    )


def _build_discovery_payload(
    ctx: PluginContext,
    *,
    plugin_id: str,
) -> dict[str, object]:
    host_plugin_id: str | None = None
    host_conf_obj = ctx.pdata.get("host")
    if isinstance(host_conf_obj, dict):
        host_plugin_id_obj = host_conf_obj.get("plugin_id")
        if isinstance(host_plugin_id_obj, str) and host_plugin_id_obj:
            host_plugin_id = host_plugin_id_obj

    plugin_type = str(ctx.pdata.get("type", "plugin") or "plugin")
    error_type: str | None = None
    error_message: str | None = None
    error_phase: str | None = None

    if not ctx.enabled:
        entries_preview = _extract_entries_preview(
            plugin_id,
            cls=type("DisabledPluginStub", (), {}),
            conf=ctx.conf,
            pdata=ctx.pdata,
        )
    else:
        entries_preview: list[dict[str, object]]
        dependency_errors: list[str] = []
        for dep in ctx.dependencies:
            satisfied, dep_error = _check_plugin_dependency(dep, logger, plugin_id)
            if not satisfied:
                dependency_errors.append(str(dep_error or "dependency check failed"))
                break
        if dependency_errors:
            error_type = "DependencyCheckFailed"
            error_message = dependency_errors[0]
            error_phase = "dependency_check"
            entries_preview = _extract_entries_preview(
                plugin_id,
                cls=type("FailedPluginStub", (), {}),
                conf=ctx.conf,
                pdata=ctx.pdata,
            )
        else:
            missing_requirements = _find_missing_python_requirements(ctx.python_requirements)
            if missing_requirements:
                error_type = "MissingPythonDependencies"
                error_message = f"Unsatisfied Python dependencies: {missing_requirements}"
                error_phase = "python_requirements"
                entries_preview = _extract_entries_preview(
                    plugin_id,
                    cls=type("FailedPluginStub", (), {}),
                    conf=ctx.conf,
                    pdata=ctx.pdata,
                )
            else:
                try:
                    module_path, class_name = ctx.entry.split(":", 1)
                    module_obj = importlib.import_module(module_path)
                    cls_obj = getattr(module_obj, class_name)
                    entries_preview = _extract_entries_preview(plugin_id, cls_obj, ctx.conf, ctx.pdata)
                except (ImportError, ModuleNotFoundError) as exc:
                    error_type = type(exc).__name__
                    error_message = str(exc)
                    error_phase = "import_module"
                    entries_preview = _extract_entries_preview(
                        plugin_id,
                        cls=type("FailedPluginStub", (), {}),
                        conf=ctx.conf,
                        pdata=ctx.pdata,
                    )
                except AttributeError as exc:
                    error_type = "AttributeError"
                    error_message = f"Class not found for entry '{ctx.entry}': {exc}"
                    error_phase = "import_class"
                    entries_preview = _extract_entries_preview(
                        plugin_id,
                        cls=type("FailedPluginStub", (), {}),
                        conf=ctx.conf,
                        pdata=ctx.pdata,
                    )

    plugin_meta = _build_plugin_meta(
        plugin_id,
        ctx.pdata,
        sdk_supported_str=ctx.sdk_supported_str,
        sdk_recommended_str=ctx.sdk_recommended_str,
        sdk_untested_str=ctx.sdk_untested_str,
        sdk_conflicts_list=ctx.sdk_conflicts_list,
        dependencies=ctx.dependencies,
        host_plugin_id=host_plugin_id,
        plugin_ui=_extract_plugin_ui_config(ctx.conf, plugin_id=plugin_id, logger=logger),
    )
    payload = plugin_meta.model_dump(mode="python")
    payload["config_path"] = str(ctx.toml_path)
    payload["entry_point"] = ctx.entry
    payload["runtime_enabled"] = bool(ctx.enabled)
    payload["runtime_auto_start"] = bool(ctx.auto_start) if plugin_type != "extension" else False
    payload["entries_preview"] = entries_preview
    payload["plugin_type"] = plugin_type
    if plugin_type == "adapter":
        adapter_conf = ctx.conf.get("adapter")
        if isinstance(adapter_conf, dict):
            payload["adapter_mode"] = str(adapter_conf.get("mode", "hybrid") or "hybrid")

    if error_type and error_message and error_phase:
        payload["runtime_load_state"] = "failed"
        payload["runtime_load_error_type"] = error_type
        payload["runtime_load_error_message"] = error_message
        payload["runtime_load_error_phase"] = error_phase
    else:
        payload.pop("runtime_load_state", None)
        payload.pop("runtime_load_error_type", None)
        payload.pop("runtime_load_error_message", None)
        payload.pop("runtime_load_error_phase", None)

    payload.pop("runtime_source_missing", None)
    return payload


def _build_discovery_record_from_context(ctx: PluginContext) -> PluginDiscoveryRecord:
    payload = _build_discovery_payload(ctx, plugin_id=ctx.pid)
    return PluginDiscoveryRecord(
        plugin_id=ctx.pid,
        original_plugin_id=ctx.pid,
        config_path=ctx.toml_path,
        entry_point=ctx.entry,
        plugin_type=str(ctx.pdata.get("type", "plugin") or "plugin"),
        enabled=bool(ctx.enabled),
        auto_start=bool(ctx.auto_start),
        meta_payload=payload,
    )


def _apply_discovery_record_sync(
    record: PluginDiscoveryRecord,
    *,
    existing_snapshot: dict[str, dict[str, object]] | None = None,
    preferred_runtime_plugin_id: str | None = None,
) -> tuple[str, dict[str, object]]:
    target_plugin_id = preferred_runtime_plugin_id
    if target_plugin_id is None and existing_snapshot is not None:
        target_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
            record.config_path,
            existing_snapshot,
        )
    if target_plugin_id is None:
        target_plugin_id = record.plugin_id

    runtime_plugin_id = _resolve_plugin_id_conflict(
        target_plugin_id,
        logger,
        config_path=record.config_path,
        entry_point=record.entry_point,
        plugin_data=record.meta_payload,
        purpose="register",
        enable_rename=True,
    )
    if runtime_plugin_id is None:
        raise ServerDomainError(
            code="PLUGIN_REGISTRY_CONFLICT",
            message=f"Plugin '{record.plugin_id}' could not be registered due to an ID conflict",
            status_code=409,
            details={"plugin_id": record.plugin_id},
        )

    plugin_meta = _build_plugin_meta(
        runtime_plugin_id,
        {
            "name": record.meta_payload.get("name", runtime_plugin_id),
            "type": record.meta_payload.get("type", record.plugin_type),
            "description": record.meta_payload.get("description", ""),
            "short_description": record.meta_payload.get("short_description", ""),
            "keywords": record.meta_payload.get("keywords", []),
            "passive": record.meta_payload.get("passive", False),
            "version": record.meta_payload.get("version", "0.1.0"),
            "author": record.meta_payload.get("author"),
        },
        sdk_supported_str=record.meta_payload.get("sdk_supported") if isinstance(record.meta_payload.get("sdk_supported"), str) else None,
        sdk_recommended_str=record.meta_payload.get("sdk_recommended") if isinstance(record.meta_payload.get("sdk_recommended"), str) else None,
        sdk_untested_str=record.meta_payload.get("sdk_untested") if isinstance(record.meta_payload.get("sdk_untested"), str) else None,
        sdk_conflicts_list=record.meta_payload.get("sdk_conflicts") if isinstance(record.meta_payload.get("sdk_conflicts"), list) else None,
        dependencies=record.meta_payload.get("dependencies") if isinstance(record.meta_payload.get("dependencies"), list) else None,
        host_plugin_id=record.meta_payload.get("host_plugin_id") if isinstance(record.meta_payload.get("host_plugin_id"), str) else None,
        plugin_ui=record.meta_payload.get("plugin_ui") if isinstance(record.meta_payload.get("plugin_ui"), dict) else None,
    )
    resolved_id = register_plugin(
        plugin_meta,
        logger,
        config_path=record.config_path,
        entry_point=record.entry_point,
    )
    if resolved_id is None:
        raise ServerDomainError(
            code="PLUGIN_REGISTRY_CONFLICT",
            message=f"Plugin '{record.plugin_id}' could not be registered due to an ID conflict",
            status_code=409,
            details={"plugin_id": record.plugin_id},
        )

    payload = dict(record.meta_payload)
    if resolved_id != record.plugin_id:
        payload["id"] = resolved_id
        preview_obj = payload.get("entries_preview")
        if isinstance(preview_obj, list):
            payload["entries_preview"] = _remap_entries_preview_plugin_id(
                [item for item in preview_obj if isinstance(item, dict)],
                plugin_id=resolved_id,
            )

    with state.acquire_plugins_write_lock():
        current_meta = state.plugins.get(resolved_id)
        merged = dict(current_meta) if isinstance(current_meta, dict) else {}
        for key in _MANAGED_META_KEYS:
            if key in payload:
                merged[key] = payload[key]
            else:
                merged.pop(key, None)
        state.plugins[resolved_id] = merged
    state.invalidate_snapshot_cache("plugins")
    return resolved_id, payload


def _remove_stale_plugin_metadata_sync(
    stale_ids: set[str],
    *,
    running_ids: set[str],
) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    kept_running: list[str] = []
    with state.acquire_plugins_write_lock():
        for plugin_id in sorted(stale_ids):
            raw_meta = state.plugins.get(plugin_id)
            if not isinstance(raw_meta, dict):
                continue
            if plugin_id in running_ids:
                raw_meta["runtime_source_missing"] = True
                state.plugins[plugin_id] = raw_meta
                kept_running.append(plugin_id)
                continue
            state.plugins.pop(plugin_id, None)
            removed.append(plugin_id)
    if removed or kept_running:
        state.invalidate_snapshot_cache("plugins")
    return removed, kept_running


def _collect_missing_plugin_ids_sync(existing_snapshot: dict[str, dict[str, object]]) -> set[str]:
    missing_ids: set[str] = set()
    for plugin_id, meta in existing_snapshot.items():
        config_path_obj = meta.get("config_path")
        if not isinstance(config_path_obj, str) or not config_path_obj:
            continue
        try:
            config_path = Path(config_path_obj).resolve()
        except Exception:
            config_path = Path(config_path_obj)
        if not config_path.exists():
            missing_ids.add(plugin_id)
    return missing_ids


def _get_autostart_plugin_ids_sync() -> list[str]:
    candidates: set[str] = set()
    with state.acquire_plugins_read_lock():
        for plugin_id, raw_meta in state.plugins.items():
            if not isinstance(plugin_id, str) or not isinstance(raw_meta, dict):
                continue
            if raw_meta.get("type") == "extension":
                continue
            if raw_meta.get("runtime_enabled") is False:
                continue
            if raw_meta.get("runtime_auto_start") is False:
                continue
            if raw_meta.get("runtime_load_state") == "failed":
                continue
            if raw_meta.get("runtime_source_missing") is True:
                continue
            candidates.add(plugin_id)
    return _build_ordered_plugin_ids_sync(candidates)


class PluginRegistryService:
    async def refresh_registry(self) -> dict[str, object]:
        return await asyncio.to_thread(self._refresh_registry_sync)

    async def refresh_plugin(self, plugin_id: str) -> dict[str, object]:
        return await asyncio.to_thread(self._refresh_plugin_sync, plugin_id)

    async def list_autostart_plugin_ids(self) -> list[str]:
        return await asyncio.to_thread(_get_autostart_plugin_ids_sync)

    async def order_plugin_ids(self, plugin_ids: list[str]) -> list[str]:
        return await asyncio.to_thread(self._order_plugin_ids_sync, plugin_ids)

    async def list_extension_configs_for_host(self, host_plugin_id: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._list_extension_configs_for_host_sync, host_plugin_id)

    def _refresh_registry_sync(self) -> dict[str, object]:
        roots = tuple(PLUGIN_CONFIG_ROOTS)
        _prepare_plugin_import_roots(roots, logger)

        existing_snapshot = _get_registered_plugin_snapshot_sync()
        running_ids = _list_running_plugin_ids_sync()
        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        snapshot = _discover_registry_snapshot_sync(roots)
        failed = [
            {
                "plugin_id": item.plugin_id or "",
                "config_path": str(item.config_path),
                "error": item.error,
            }
            for item in snapshot.failures
        ]

        for record in snapshot.records:
            try:
                previous_runtime_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
                    record.config_path,
                    existing_snapshot,
                )
                previous_plugin_id = previous_runtime_plugin_id or record.plugin_id
                previous_managed = _select_managed_fields(existing_snapshot.get(previous_plugin_id, {}))
                resolved_id, payload = _apply_discovery_record_sync(
                    record,
                    existing_snapshot=existing_snapshot,
                    preferred_runtime_plugin_id=previous_runtime_plugin_id,
                )
                current_managed = _select_managed_fields(payload)
                if resolved_id not in existing_snapshot:
                    added.append(resolved_id)
                elif previous_managed == current_managed:
                    unchanged.append(resolved_id)
                else:
                    updated.append(resolved_id)
            except ServerDomainError as exc:
                failed.append(
                    {
                        "plugin_id": record.plugin_id,
                        "config_path": str(record.config_path),
                        "error": exc.message,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "refresh_registry failed for plugin {}: err_type={}, err={}",
                    record.plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
                failed.append(
                    {
                        "plugin_id": record.plugin_id,
                        "config_path": str(record.config_path),
                        "error": str(exc),
                    }
                )

        missing_ids = _collect_missing_plugin_ids_sync(existing_snapshot)
        removed, removed_running = _remove_stale_plugin_metadata_sync(missing_ids, running_ids=running_ids)
        return {
            "success": not failed,
            "added": added,
            "updated": updated,
            "removed": removed,
            "removed_running": removed_running,
            "unchanged": unchanged,
            "failed": failed,
            "scanned_count": len(snapshot.records) + len(snapshot.failures),
        }

    def _refresh_plugin_sync(self, plugin_id: str) -> dict[str, object]:
        normalized_plugin_id = plugin_id.strip()
        if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
            raise ServerDomainError(
                code="PLUGIN_INVALID_ID",
                message="Invalid plugin id",
                status_code=400,
                details={"plugin_id": plugin_id},
            )

        roots = tuple(PLUGIN_CONFIG_ROOTS)
        existing_snapshot = _get_registered_plugin_snapshot_sync()
        config_path = _resolve_meta_config_path(existing_snapshot.get(normalized_plugin_id))
        if config_path is None or not config_path.exists():
            config_path = _find_plugin_config_path(normalized_plugin_id, roots)
        if config_path is None:
            raise ServerDomainError(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{normalized_plugin_id}' configuration not found",
                status_code=404,
                details={"plugin_id": normalized_plugin_id},
            )

        _prepare_plugin_import_roots(roots, logger)
        ctx = _parse_single_plugin_config(config_path, set(), logger)
        if ctx is None:
            raise ServerDomainError(
                code="PLUGIN_DISCOVERY_FAILED",
                message=f"Plugin '{normalized_plugin_id}' configuration could not be parsed",
                status_code=400,
                details={"plugin_id": normalized_plugin_id},
            )

        record = _build_discovery_record_from_context(ctx)
        previous_runtime_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
            config_path,
            existing_snapshot,
        )
        previous_plugin_id = previous_runtime_plugin_id or normalized_plugin_id
        previous_managed = _select_managed_fields(existing_snapshot.get(previous_plugin_id, {}))
        resolved_id, payload = _apply_discovery_record_sync(
            record,
            existing_snapshot=existing_snapshot,
            preferred_runtime_plugin_id=previous_runtime_plugin_id,
        )
        current_managed = _select_managed_fields(payload)
        status = "added"
        if previous_plugin_id in existing_snapshot:
            status = "unchanged" if previous_managed == current_managed else "updated"

        return {
            "success": True,
            "plugin_id": resolved_id,
            "original_plugin_id": normalized_plugin_id,
            "status": status,
            "config_path": str(config_path),
        }

    def _order_plugin_ids_sync(self, plugin_ids: list[str]) -> list[str]:
        return _build_ordered_plugin_ids_sync({plugin_id for plugin_id in plugin_ids if isinstance(plugin_id, str)})

    def _list_extension_configs_for_host_sync(self, host_plugin_id: str) -> list[dict[str, str]]:
        extension_configs: list[dict[str, str]] = []
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
            if meta.get("runtime_enabled") is False:
                continue
            if meta.get("runtime_source_missing") is True:
                continue

            entry_point_obj = meta.get("entry_point")
            if not isinstance(entry_point_obj, str) or ":" not in entry_point_obj:
                continue

            prefix = ""
            config_path = _resolve_meta_config_path(meta)
            if config_path is not None:
                prefix = _read_extension_prefix_sync(config_path)

            extension_configs.append(
                {
                    "ext_id": plugin_id,
                    "ext_entry": entry_point_obj,
                    "prefix": prefix,
                }
            )

        extension_configs.sort(key=lambda item: item["ext_id"])
        return extension_configs

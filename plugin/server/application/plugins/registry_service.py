from __future__ import annotations

import asyncio
import re
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plugin.core.dependency import _topological_sort_plugins
from plugin.core.entry_points import describe_plugin_entry_directory_mismatch
from plugin.core.host import _import_plugin_module
from plugin.core.registry import (
    PluginContext,
    _build_plugin_meta,
    _check_plugin_dependency,
    _ensure_python_requirement_paths,
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
from plugin.server.application.plugins.inventory_store import (
    get_deleted_plugin_ids,
    get_inventory_resolution,
    load_inventory_resolution_for_registry,
    resolve_inventory_path,
)
from plugin.server.application.plugins.installation_selection import (
    inspect_plugin_installations,
    serialize_plugin_installation_projection,
)
from plugin.server.application.plugins.resolver import (
    PluginCandidate,
    resolve_plugin_candidates,
)
from plugin.server.application.plugins.mutation_guard import (
    plugin_mutation_guard,
    plugin_mutation_guard_is_held_by_current_task,
)
from plugin.server.application.plugins.upgrade_support import (
    ReplacementRecoveryResult,
    await_cancellation_safe,
    recover_incomplete_plugin_replacements,
)
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
    selected_contexts: list[PluginContext]
    failures: list[PluginDiscoveryFailure]
    config_paths: set[Path]
    candidate_config_paths: set[Path]
    candidate_ids_by_path: dict[Path, str]
    selected_config_paths: set[Path]
    resolution_warnings: list[dict[str, str]]


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
    canonical_plugin_id = normalized_plugin_id.casefold()
    inventory = get_inventory_resolution()
    if canonical_plugin_id in inventory.deleted_plugin_ids:
        return None

    active_user_directory = inventory.active_user_directories.get(canonical_plugin_id)
    if active_user_directory is not None and len(roots) > 1:
        active = inventory.active_installations.get(canonical_plugin_id)
        candidate_roots = roots[1:]
        if len(roots) >= 3 and active is not None:
            candidate_roots = roots[1:2] if active.installation_kind == "managed" else roots[2:]
        for root in candidate_roots:
            resolved_root = root.resolve()
            config_file = (resolved_root / active_user_directory / "plugin.toml").resolve()
            if resolved_root in config_file.parents and config_file.exists():
                return config_file

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


def _candidate_root_id(config_path: Path, roots: tuple[Path, ...]) -> str:
    try:
        resolved_path = config_path.resolve()
    except Exception:
        resolved_path = config_path
    for index, root in enumerate(roots):
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root
        if resolved_root in resolved_path.parents:
            if len(roots) > 1 and index == 0:
                return "builtin"
            if len(roots) >= 3 and index == 1:
                return "managed"
            return "user"
    raise ValueError("plugin candidate is outside configured roots")


def _resolve_plugin_contexts(
    contexts: list[PluginContext],
    *,
    roots: tuple[Path, ...],
) -> tuple[list[PluginContext], list[PluginDiscoveryFailure], list[dict[str, str]]]:
    context_by_path: dict[Path, PluginContext] = {}
    candidates: list[PluginCandidate] = []
    failures: list[PluginDiscoveryFailure] = []
    for ctx in contexts:
        try:
            config_path = ctx.toml_path.resolve()
            root_id = _candidate_root_id(config_path, roots)
        except Exception as exc:
            failures.append(
                PluginDiscoveryFailure(
                    plugin_id=ctx.pid,
                    config_path=ctx.toml_path,
                    error=f"candidate classification failed: {type(exc).__name__}",
                )
            )
            continue
        context_by_path[config_path] = ctx
        candidates.append(
            PluginCandidate(
                logical_plugin_id=ctx.pid,
                root_id=root_id,  # type: ignore[arg-type]
                directory_name=config_path.parent.name,
                config_path=config_path,
            )
        )

    inventory, inventory_issue = load_inventory_resolution_for_registry()
    if inventory_issue is not None:
        failures.append(
            PluginDiscoveryFailure(
                plugin_id="__inventory__",
                config_path=resolve_inventory_path(),
                error=inventory_issue,
            )
        )

    selected: list[PluginContext] = []
    warnings: list[dict[str, str]] = []
    for resolution in resolve_plugin_candidates(
        candidates,
        inventory=inventory,
    ):
        if resolution.status == "blocked":
            failures.append(
                PluginDiscoveryFailure(
                    plugin_id=resolution.logical_plugin_id,
                    config_path=resolution.rejected[0].config_path,
                    error=resolution.reason,
                )
            )
            continue
        if resolution.selected is not None:
            selected.append(context_by_path[resolution.selected.config_path])
        if resolution.reason in {
            "builtin_default",
            "missing_user_installation_fallback_builtin",
        } and resolution.rejected:
            warnings.append(
                {
                    "plugin_id": resolution.logical_plugin_id,
                    "reason": resolution.reason,
                }
            )
    return selected, failures, warnings


def _collect_plugin_contexts_from_roots_sync(
    roots: tuple[Path, ...],
) -> tuple[list[PluginContext], dict[str, PluginContext]]:
    discovered_contexts: list[PluginContext] = []
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
            discovered_contexts.append(ctx)

    plugin_contexts, _, _ = _resolve_plugin_contexts(discovered_contexts, roots=roots)
    pid_to_context = {ctx.pid: ctx for ctx in plugin_contexts}
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


def _discover_registry_snapshot_sync(
    roots: tuple[Path, ...],
    *,
    classification_roots: tuple[Path, ...] | None = None,
) -> PluginDiscoverySnapshot:
    processed_paths: set[Path] = set()
    failures: list[PluginDiscoveryFailure] = []
    config_paths: set[Path] = set()
    discovered_contexts: list[PluginContext] = []

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

            discovered_contexts.append(ctx)

    selected_contexts, resolution_failures, resolution_warnings = _resolve_plugin_contexts(
        discovered_contexts,
        roots=classification_roots or roots,
    )
    failures.extend(resolution_failures)
    candidate_config_paths = {ctx.toml_path.resolve() for ctx in discovered_contexts}
    candidate_ids_by_path = {
        ctx.toml_path.resolve(): ctx.pid
        for ctx in discovered_contexts
    }
    selected_config_paths = {ctx.toml_path.resolve() for ctx in selected_contexts}

    return PluginDiscoverySnapshot(
        selected_contexts=selected_contexts,
        failures=failures,
        config_paths=config_paths,
        candidate_config_paths=candidate_config_paths,
        candidate_ids_by_path=candidate_ids_by_path,
        selected_config_paths=selected_config_paths,
        resolution_warnings=resolution_warnings,
    )


def _build_discovery_payload(
    ctx: PluginContext,
    *,
    plugin_id: str,
) -> dict[str, object]:
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
        entry_mismatch = describe_plugin_entry_directory_mismatch(
            ctx.entry,
            config_path=ctx.toml_path,
        )
        if entry_mismatch:
            error_type = "PluginEntryDirectoryMismatch"
            error_message = entry_mismatch
            error_phase = "entry_validation"
            entries_preview = _extract_entries_preview(
                plugin_id,
                cls=type("FailedPluginStub", (), {}),
                conf=ctx.conf,
                pdata=ctx.pdata,
            )
        else:
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
                missing_requirements = _find_missing_python_requirements(
                    ctx.python_requirements,
                    search_paths=ctx.python_requirement_paths,
                )
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
                    # The startup loader installs vendor paths on sys.path before
                    # importing each plugin's entry module; do the same here so a
                    # plugin whose [project].dependencies live only under its own
                    # vendor/ directory does not get falsely recorded as
                    # ImportError/ModuleNotFoundError during a registry refresh.
                    _ensure_python_requirement_paths(
                        ctx.python_requirement_paths,
                        logger,
                        plugin_id,
                    )
                    try:
                        module_path, class_name = ctx.entry.split(":", 1)
                        module_obj = _import_plugin_module(module_path, ctx.toml_path, logger)
                        cls_obj = getattr(module_obj, class_name)
                        entries_preview = _extract_entries_preview(plugin_id, cls_obj, ctx.conf, ctx.pdata)
                    except (ImportError, ModuleNotFoundError, SyntaxError) as exc:
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
        plugin_ui=_extract_plugin_ui_config(ctx.conf, plugin_id=plugin_id, logger=logger),
    )
    payload = plugin_meta.model_dump(mode="python")
    payload["config_path"] = str(ctx.toml_path)
    payload["entry_point"] = ctx.entry
    payload["runtime_enabled"] = bool(ctx.enabled)
    payload["runtime_auto_start"] = bool(ctx.auto_start)
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
) -> tuple[str, dict[str, object]]:
    runtime_plugin_id = _resolve_plugin_id_conflict(
        record.plugin_id,
        logger,
        config_path=record.config_path,
        entry_point=record.entry_point,
        plugin_data=record.meta_payload,
        purpose="register",
        enable_rename=False,
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
        plugin_ui=record.meta_payload.get("plugin_ui") if isinstance(record.meta_payload.get("plugin_ui"), dict) else None,
    )
    resolved_id = register_plugin(
        plugin_meta,
        logger,
        config_path=record.config_path,
        entry_point=record.entry_point,
        enable_rename=False,
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
    @staticmethod
    async def _recover_incomplete_replacements():
        from plugin import settings
        from plugin.server.application.plugins.lifecycle_service import (
            recover_incomplete_plugin_deletions,
        )

        plugin_roots = tuple(
            dict.fromkeys(
                (
                    Path(settings.MANAGED_PLUGIN_INSTALLATIONS_ROOT)
                    .expanduser()
                    .resolve(),
                    Path(settings.USER_PLUGIN_CONFIG_ROOT).expanduser().resolve(),
                )
            )
        )
        package_profiles_root = Path(
            settings.USER_PACKAGE_PROFILES_ROOT
        ).expanduser().resolve()
        results = []
        for plugin_root in plugin_roots:
            for recovery_call, kwargs in (
                (
                    recover_incomplete_plugin_replacements,
                    {
                        "journal_root": plugin_root
                        / ".upgrade-backups"
                        / ".transactions",
                        "allowed_roots": (plugin_root, package_profiles_root),
                    },
                ),
                (
                    recover_incomplete_plugin_deletions,
                    {
                        "journal_root": plugin_root
                        / ".delete-backups"
                        / ".transactions",
                        "user_root": plugin_root,
                    },
                ),
            ):
                operation = asyncio.create_task(
                    asyncio.to_thread(recovery_call, **kwargs)
                )
                try:
                    results.append(await asyncio.shield(operation))
                except asyncio.CancelledError:
                    await await_cancellation_safe(operation)
                    raise
        first, *remaining = results
        return type(first)(
            tuple(
                operation_id
                for result in results
                for operation_id in result.recovered_operation_ids
            ),
            tuple(
                operation_id
                for result in results
                for operation_id in result.manual_recovery_operation_ids
            ),
            tuple(
                dict.fromkeys(
                    plugin_id
                    for result in results
                    for plugin_id in result.manual_recovery_plugin_ids
                )
            ),
            first.block_user_plugin_root
            or any(result.block_user_plugin_root for result in remaining),
        )

    async def refresh_registry(
        self,
        *,
        _mutation_guarded: bool = False,
        _recover_incomplete: bool = True,
    ) -> dict[str, object]:
        if not _mutation_guarded:
            nested_mutation = plugin_mutation_guard_is_held_by_current_task()
            async with plugin_mutation_guard():
                return await self.refresh_registry(
                    _mutation_guarded=True,
                    _recover_incomplete=not nested_mutation,
                )
        recovery = (
            await self._recover_incomplete_replacements()
            if _recover_incomplete
            else ReplacementRecoveryResult((), ())
        )
        return await self._await_refresh_worker(
            self._refresh_registry_sync,
            blocked_recovery_plugin_ids=frozenset(
                recovery.manual_recovery_plugin_ids
            ),
            block_user_plugin_root=recovery.block_user_plugin_root,
        )

    async def refresh_plugin(
        self,
        plugin_id: str,
        *,
        _mutation_guarded: bool = False,
        _recover_incomplete: bool = True,
    ) -> dict[str, object]:
        if not _mutation_guarded:
            nested_mutation = plugin_mutation_guard_is_held_by_current_task()
            async with plugin_mutation_guard():
                return await self.refresh_plugin(
                    plugin_id,
                    _mutation_guarded=True,
                    _recover_incomplete=not nested_mutation,
                )
        recovery = (
            await self._recover_incomplete_replacements()
            if _recover_incomplete
            else ReplacementRecoveryResult((), ())
        )
        return await self._await_refresh_worker(
            self._refresh_plugin_sync,
            plugin_id,
            blocked_recovery_plugin_ids=frozenset(
                recovery.manual_recovery_plugin_ids
            ),
            block_user_plugin_root=recovery.block_user_plugin_root,
        )

    @staticmethod
    async def _await_refresh_worker(function, /, *args, **kwargs):
        operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            await await_cancellation_safe(operation)
            raise

    async def list_autostart_plugin_ids(self) -> list[str]:
        return await asyncio.to_thread(_get_autostart_plugin_ids_sync)

    async def list_plugin_installations(self, plugin_id: str) -> dict[str, object]:
        try:
            projection = await self._await_refresh_worker(
                inspect_plugin_installations,
                plugin_id,
            )
        except Exception as exc:
            raise ServerDomainError(
                code="PLUGIN_INSTALLATIONS_UNAVAILABLE",
                message="plugin installations could not be inspected",
                status_code=409,
                details={
                    "plugin_id": plugin_id,
                    "reason": type(exc).__name__,
                },
            ) from exc
        return serialize_plugin_installation_projection(projection)

    async def order_plugin_ids(self, plugin_ids: list[str]) -> list[str]:
        return await asyncio.to_thread(self._order_plugin_ids_sync, plugin_ids)

    def _refresh_registry_sync(
        self,
        only_plugin_id: str | None = None,
        *,
        blocked_recovery_plugin_ids: frozenset[str] = frozenset(),
        block_user_plugin_root: bool = False,
    ) -> dict[str, object]:
        roots = tuple(PLUGIN_CONFIG_ROOTS)
        scan_roots = roots[:1] if block_user_plugin_root and len(roots) > 1 else roots
        _prepare_plugin_import_roots(scan_roots, logger)

        existing_snapshot = _get_registered_plugin_snapshot_sync()
        running_ids = _list_running_plugin_ids_sync()
        added: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        snapshot = _discover_registry_snapshot_sync(
            scan_roots,
            classification_roots=roots,
        )
        selected_contexts = [
            ctx
            for ctx in snapshot.selected_contexts
            if (only_plugin_id is None or ctx.pid == only_plugin_id)
            and ctx.pid.casefold() not in blocked_recovery_plugin_ids
        ]
        candidate_config_paths = {
            path
            for path, plugin_id in snapshot.candidate_ids_by_path.items()
            if only_plugin_id is None or plugin_id == only_plugin_id
        }
        selected_config_paths = {
            ctx.toml_path.resolve()
            for ctx in selected_contexts
        }
        failed = [
            {
                "plugin_id": item.plugin_id or "",
                "config_path": str(item.config_path),
                "error": item.error,
            }
            for item in snapshot.failures
            if only_plugin_id is None or item.plugin_id == only_plugin_id
        ]
        failed.extend(
            {
                "plugin_id": plugin_id,
                "config_path": "",
                "error": "replacement_needs_manual_recovery",
            }
            for plugin_id in sorted(blocked_recovery_plugin_ids)
            if only_plugin_id is None or plugin_id == only_plugin_id.casefold()
        )
        if block_user_plugin_root:
            failed.append(
                {
                    "plugin_id": only_plugin_id or "__replacement_recovery__",
                    "config_path": "",
                    "error": "user_plugin_root_requires_manual_recovery",
                }
            )
        superseded_ids: set[str] = set()
        selected_id_by_path = {
            ctx.toml_path.resolve(): ctx.pid
            for ctx in selected_contexts
        }
        for existing_plugin_id, existing_meta in existing_snapshot.items():
            existing_config_path = _resolve_meta_config_path(existing_meta)
            if existing_config_path is None:
                continue
            if (
                existing_config_path in candidate_config_paths
                and (
                    existing_config_path not in selected_config_paths
                    or selected_id_by_path.get(existing_config_path) != existing_plugin_id
                )
            ):
                superseded_ids.add(existing_plugin_id)
        for selected_config_path, selected_plugin_id in selected_id_by_path.items():
            existing_config_path = _resolve_meta_config_path(
                existing_snapshot.get(selected_plugin_id)
            )
            if (
                existing_config_path is not None
                and existing_config_path != selected_config_path
            ):
                superseded_ids.add(selected_plugin_id)
        superseded_running = sorted(superseded_ids & running_ids)
        superseded_removed, _ = _remove_stale_plugin_metadata_sync(
            superseded_ids - running_ids,
            running_ids=set(),
        )
        for plugin_id in superseded_removed:
            existing_snapshot.pop(plugin_id, None)
        blocked_activation_ids: set[str] = set()
        for plugin_id in superseded_running:
            existing_config_path = _resolve_meta_config_path(existing_snapshot.get(plugin_id))
            blocked_plugin_id = (
                selected_id_by_path.get(existing_config_path, plugin_id)
                if existing_config_path is not None
                else plugin_id
            )
            blocked_activation_ids.add(blocked_plugin_id)
            failed.append(
                {
                    "plugin_id": blocked_plugin_id,
                    "config_path": "",
                    "error": "running plugin prevents activation switch",
                }
            )

        records: list[PluginDiscoveryRecord] = []
        for ctx in selected_contexts:
            if ctx.pid in blocked_activation_ids:
                continue
            try:
                records.append(_build_discovery_record_from_context(ctx))
            except Exception as exc:
                logger.warning(
                    "plugin discovery payload failed for {}: err_type={}, err={}",
                    ctx.toml_path,
                    type(exc).__name__,
                    str(exc),
                )
                failed.append(
                    {
                        "plugin_id": ctx.pid or ctx.toml_path.parent.name or "",
                        "config_path": str(ctx.toml_path),
                        "error": str(exc),
                    }
                )

        for record in records:
            try:
                previous_runtime_plugin_id = _find_existing_runtime_plugin_id_by_config_path(
                    record.config_path,
                    existing_snapshot,
                )
                previous_plugin_id = previous_runtime_plugin_id or record.plugin_id
                previous_managed = _select_managed_fields(existing_snapshot.get(previous_plugin_id, {}))
                resolved_id, payload = _apply_discovery_record_sync(record)
                current_managed = _select_managed_fields(payload)
                if resolved_id in superseded_removed:
                    updated.append(resolved_id)
                elif resolved_id not in existing_snapshot:
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
        if only_plugin_id is not None:
            missing_ids.intersection_update({only_plugin_id})
        inventory, _inventory_issue = load_inventory_resolution_for_registry()
        deleted_ids = {plugin_id.casefold() for plugin_id in inventory.deleted_plugin_ids}
        normalized_only_plugin_id = (
            only_plugin_id.casefold() if only_plugin_id is not None else None
        )
        missing_ids.update(
            registered_id
            for registered_id in existing_snapshot
            if registered_id.casefold() in deleted_ids
            and (
                normalized_only_plugin_id is None
                or registered_id.casefold() == normalized_only_plugin_id
            )
        )
        removed, removed_running = _remove_stale_plugin_metadata_sync(missing_ids, running_ids=running_ids)
        selected_ids = {record.plugin_id for record in records}
        removed = sorted(
            (set(removed) | set(superseded_removed)) - selected_ids
        )
        removed_running = sorted(set(removed_running) | set(superseded_running))
        return {
            "success": not failed,
            "added": added,
            "updated": updated,
            "removed": removed,
            "removed_running": removed_running,
            "unchanged": unchanged,
            "failed": failed,
            "resolution_warnings": [
                warning
                for warning in snapshot.resolution_warnings
                if only_plugin_id is None or warning.get("plugin_id") == only_plugin_id
            ],
            "scanned_count": len(records)
            + sum(
                1
                for item in snapshot.failures
                if only_plugin_id is None or item.plugin_id == only_plugin_id
            ),
        }

    def _refresh_plugin_sync(
        self,
        plugin_id: str,
        *,
        blocked_recovery_plugin_ids: frozenset[str] = frozenset(),
        block_user_plugin_root: bool = False,
    ) -> dict[str, object]:
        normalized_plugin_id = plugin_id.strip()
        if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
            raise ServerDomainError(
                code="PLUGIN_INVALID_ID",
                message="Invalid plugin id",
                status_code=400,
                details={"plugin_id": plugin_id},
            )
        if normalized_plugin_id.casefold() in get_deleted_plugin_ids():
            raise ServerDomainError(
                code="PLUGIN_DELETED_BY_USER",
                message=f"Plugin '{normalized_plugin_id}' was deleted by the user",
                status_code=404,
                details={"plugin_id": normalized_plugin_id},
            )

        refresh_result = self._refresh_registry_sync(
            only_plugin_id=normalized_plugin_id,
            blocked_recovery_plugin_ids=blocked_recovery_plugin_ids,
            block_user_plugin_root=block_user_plugin_root,
        )
        matching_failure = next(
            (
                failure
                for failure in refresh_result.get("failed", [])
                if isinstance(failure, dict)
                and failure.get("plugin_id") == normalized_plugin_id
            ),
            None,
        )
        if matching_failure is not None:
            raise ServerDomainError(
                code="PLUGIN_RESOLUTION_BLOCKED",
                message=f"Plugin '{normalized_plugin_id}' could not be selected safely",
                status_code=409,
                details={
                    "plugin_id": normalized_plugin_id,
                    "reason": str(matching_failure.get("error") or "resolution_failed"),
                },
            )

        current_snapshot = _get_registered_plugin_snapshot_sync()
        current_meta = current_snapshot.get(normalized_plugin_id)
        config_path = _resolve_meta_config_path(current_meta)
        if config_path is None:
            raise ServerDomainError(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{normalized_plugin_id}' configuration not found",
                status_code=404,
                details={"plugin_id": normalized_plugin_id},
            )

        status = "unchanged"
        if normalized_plugin_id in refresh_result.get("added", []):
            status = "added"
        elif normalized_plugin_id in refresh_result.get("updated", []):
            status = "updated"

        return {
            "success": True,
            "plugin_id": normalized_plugin_id,
            "original_plugin_id": normalized_plugin_id,
            "status": status,
            "config_path": str(config_path),
        }

    def _order_plugin_ids_sync(self, plugin_ids: list[str]) -> list[str]:
        return _build_ordered_plugin_ids_sync({plugin_id for plugin_id in plugin_ids if isinstance(plugin_id, str)})

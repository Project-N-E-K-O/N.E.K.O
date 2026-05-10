from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from plugin.core.state import state
from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.sdk.shared.i18n import load_plugin_i18n_from_meta, resolve_i18n_refs
from plugin.server.application.plugins.ui_query_service import _build_plugin_list_actions_from_meta
from plugin.server.domain import IO_RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.utils.time_utils import now_iso

logger = get_logger("server.application.plugins.query")


# Default install_source sub-object injected when the manager is
# unavailable/degraded or when a plugin cannot be matched to any lock
# entry (Req 15.6 / design §11.2). Kept as a module-local constant so
# tests can monkeypatch and callers can ``.copy()`` it cheaply.
_DEFAULT_INSTALL_SOURCE: dict[str, object] = {
    "source": "unknown",
    "reason": None,
    "installed_at": None,
    "source_detail": None,
}


def _attach_install_source(
    plugin_info: dict[str, object],
    plugin_id: str,
    plugin_meta: Mapping[str, object],
) -> None:
    """Inject the ``install_source`` sub-object into ``plugin_info`` (Req 15).

    Derives the plugin's on-disk directory from ``plugin_meta["config_path"]``
    (the path of the plugin's ``plugin.toml``) when available — this is the
    single source of truth for the plugin's directory in the state
    snapshot. When ``config_path`` is missing or malformed we fall back
    to a ``None`` directory_path, which forces
    :meth:`InstallSourceManager.to_api_view` into its plugin_id text-match
    branch — still correct, just less precise.

    The whole attach path is defensive: any failure injects
    ``_DEFAULT_INSTALL_SOURCE`` instead of raising, so that ``/plugins``
    responses stay 200 even when the install-source subsystem is
    degraded or unavailable (Req 15.6 / 17.2).
    """

    try:
        from plugin.server.application.install_source import (
            get_install_source_manager,
        )
    except Exception as exc:  # pragma: no cover — defensive import
        logger.warning(
            "install_source import failed, using default: err={}", exc
        )
        plugin_info["install_source"] = _DEFAULT_INSTALL_SOURCE.copy()
        return

    mgr = get_install_source_manager()
    if mgr is None or mgr.is_degraded:
        plugin_info["install_source"] = _DEFAULT_INSTALL_SOURCE.copy()
        return

    # Resolve directory_path from plugin_meta["config_path"] — the
    # plugin.toml path set at registration time (see
    # plugin.core.registry.register_plugin). Open-question §17.1
    # acknowledges this is the best available source today.
    directory_path: Path | None = None
    config_path_obj = plugin_meta.get("config_path")
    if isinstance(config_path_obj, str) and config_path_obj:
        try:
            directory_path = Path(config_path_obj).parent
        except (OSError, ValueError):
            directory_path = None

    try:
        plugin_info["install_source"] = mgr.to_api_view(
            plugin_id, directory_path=directory_path
        )
    except Exception as exc:  # pragma: no cover — to_api_view should never raise
        logger.warning(
            "install_source: to_api_view failed for plugin_id=%s err=%s",
            plugin_id, exc,
        )
        plugin_info["install_source"] = _DEFAULT_INSTALL_SOURCE.copy()


def _normalize_mapping(
    raw: Mapping[object, object],
    *,
    context: str,
) -> dict[str, object]:
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


def _normalize_plugin_entries(raw_items: list[object]) -> list[dict[str, object]]:
    normalized_items: list[dict[str, object]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message="plugin list item is not an object",
                status_code=500,
                details={"index": index, "item_type": type(item).__name__},
            )
        normalized_items.append(_normalize_mapping(item, context=f"plugin_list[{index}]"))
    return normalized_items


def _normalize_string_list(raw_value: object) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    fields: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        field_name = item.strip()
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        fields.append(field_name)
    return fields


def _extract_llm_result_fields(raw_value: object, *, raw_schema: object = None) -> list[str]:
    fields = _normalize_string_list(raw_value)
    if fields:
        return fields
    if isinstance(raw_schema, Mapping):
        properties_obj = raw_schema.get("properties")
        if isinstance(properties_obj, Mapping):
            return [
                key_obj
                for key_obj in properties_obj.keys()
                if isinstance(key_obj, str) and key_obj.strip()
            ]
    return []


def _to_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _resolve_plugin_status(
    *,
    plugin_id: str,
    plugin_meta: Mapping[str, object],
    running_plugin_ids: set[str],
) -> str:
    runtime_source_missing_obj = plugin_meta.get("runtime_source_missing")
    if runtime_source_missing_obj is True:
        return "source_missing"

    runtime_load_state_obj = plugin_meta.get("runtime_load_state")
    if isinstance(runtime_load_state_obj, str) and runtime_load_state_obj == "failed":
        return "load_failed"

    plugin_type = plugin_meta.get("type")
    if plugin_type == "extension":
        runtime_enabled = _to_bool(plugin_meta.get("runtime_enabled"), default=True)
        if not runtime_enabled:
            return "disabled"

        host_plugin_id = plugin_meta.get("host_plugin_id")
        if isinstance(host_plugin_id, str) and host_plugin_id in running_plugin_ids:
            return "injected"
        return "pending"

    return "running" if plugin_id in running_plugin_ids else "stopped"


def _resolve_default_locale() -> str:
    try:
        from utils.language_utils import get_global_language_full
        return str(get_global_language_full() or "en")
    except Exception:
        return "en"


def _build_entries_from_handlers(
    *,
    plugin_id: str,
    handlers_snapshot: Mapping[object, object],
    plugin_meta: Mapping[str, object] | None = None,
    locale: str | None = None,
) -> tuple[list[dict[str, object]], set[tuple[str, str]]]:
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    prefix_dot = f"{plugin_id}."
    prefix_colon = f"{plugin_id}:plugin_entry:"

    for event_key_obj, handler_obj in handlers_snapshot.items():
        if not isinstance(event_key_obj, str):
            continue
        if not (event_key_obj.startswith(prefix_dot) or event_key_obj.startswith(prefix_colon)):
            continue

        meta = getattr(handler_obj, "meta", None)
        event_type_obj = getattr(meta, "event_type", None)
        if event_type_obj != "plugin_entry":
            continue

        raw_entry_id = getattr(meta, "id", None)
        entry_id = raw_entry_id if isinstance(raw_entry_id, str) and raw_entry_id else event_key_obj
        dedup_key = ("plugin_entry", entry_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        raw_input_schema = getattr(meta, "input_schema", {})
        input_schema: dict[str, object]
        if isinstance(raw_input_schema, Mapping):
            input_schema = _normalize_mapping(raw_input_schema, context=f"plugin_entry[{entry_id}].input_schema")
        else:
            input_schema = {}
        raw_metadata = getattr(meta, "metadata", {})
        metadata: dict[str, object]
        if isinstance(raw_metadata, Mapping):
            metadata = _normalize_mapping(raw_metadata, context=f"plugin_entry[{entry_id}].metadata")
        else:
            metadata = {}
        raw_llm_result_schema = getattr(meta, "llm_result_schema", {})
        llm_result_schema: dict[str, object]
        if isinstance(raw_llm_result_schema, Mapping):
            llm_result_schema = _normalize_mapping(
                raw_llm_result_schema,
                context=f"plugin_entry[{entry_id}].llm_result_schema",
            )
        else:
            llm_result_schema = {}
        llm_result_fields = _extract_llm_result_fields(
            getattr(meta, "llm_result_fields", None),
            raw_schema=raw_llm_result_schema,
        )

        name_obj = getattr(meta, "name", "")
        description_obj = getattr(meta, "description", "")
        return_message_obj = getattr(meta, "return_message", "")

        entry_dict: dict[str, object] = {
            "id": entry_id,
            "name": name_obj if isinstance(name_obj, (str, Mapping)) else "",
            "description": description_obj if isinstance(description_obj, (str, Mapping)) else "",
            "event_key": event_key_obj,
            "timeout": getattr(meta, "timeout", None),
            "input_schema": input_schema,
            "return_message": return_message_obj if isinstance(return_message_obj, (str, Mapping)) else "",
            "llm_result_fields": llm_result_fields,
            "llm_result_schema": llm_result_schema,
            "metadata": metadata,
        }

        # 透传 llm_result_fields 到运行时 entry，供 agent_server._lookup_llm_result_fields 读取
        meta_dict = getattr(meta, "metadata", None)
        if isinstance(meta_dict, dict) and "llm_result_fields" in meta_dict:
            entry_dict["llm_result_fields"] = meta_dict["llm_result_fields"]

        if plugin_meta is not None:
            entry_dict = resolve_i18n_refs(
                entry_dict,
                load_plugin_i18n_from_meta(plugin_meta),
                locale=locale or _resolve_default_locale(),
            )  # type: ignore[assignment]
        entries.append(entry_dict)

    return entries, seen


def _append_entries_from_preview(
    *,
    plugin_id: str,
    plugin_meta: Mapping[str, object],
    entries: list[dict[str, object]],
    seen: set[tuple[str, str]],
) -> None:
    preview_obj = plugin_meta.get("entries_preview")
    if not isinstance(preview_obj, list):
        return

    for index, preview_item in enumerate(preview_obj):
        if not isinstance(preview_item, Mapping):
            continue
        normalized_preview = _normalize_mapping(
            preview_item,
            context=f"plugins[{plugin_id}].entries_preview[{index}]",
        )

        entry_id_obj = normalized_preview.get("id")
        if not isinstance(entry_id_obj, str) or not entry_id_obj:
            continue

        dedup_key = ("plugin_entry", entry_id_obj)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        input_schema_obj = normalized_preview.get("input_schema")
        input_schema: dict[str, object]
        if isinstance(input_schema_obj, Mapping):
            input_schema = _normalize_mapping(
                input_schema_obj,
                context=f"plugins[{plugin_id}].entries_preview[{index}].input_schema",
            )
        else:
            input_schema = {}
        metadata_obj = normalized_preview.get("metadata")
        metadata: dict[str, object]
        if isinstance(metadata_obj, Mapping):
            metadata = _normalize_mapping(
                metadata_obj,
                context=f"plugins[{plugin_id}].entries_preview[{index}].metadata",
            )
        else:
            metadata = {}
        llm_result_schema_obj = normalized_preview.get("llm_result_schema")
        llm_result_schema: dict[str, object]
        if isinstance(llm_result_schema_obj, Mapping):
            llm_result_schema = _normalize_mapping(
                llm_result_schema_obj,
                context=f"plugins[{plugin_id}].entries_preview[{index}].llm_result_schema",
            )
        else:
            llm_result_schema = {}
        llm_result_fields = _extract_llm_result_fields(
            normalized_preview.get("llm_result_fields"),
            raw_schema=llm_result_schema_obj,
        )

        event_key_obj = normalized_preview.get("event_key")
        return_message_obj = normalized_preview.get("return_message")
        name_obj = normalized_preview.get("name")
        description_obj = normalized_preview.get("description")

        entry_dict: dict[str, object] = {
            "id": entry_id_obj,
            "name": name_obj if isinstance(name_obj, (str, Mapping)) else "",
            "description": description_obj if isinstance(description_obj, (str, Mapping)) else "",
            "event_key": event_key_obj if isinstance(event_key_obj, str) and event_key_obj else f"{plugin_id}.{entry_id_obj}",
            "timeout": normalized_preview.get("timeout"),
            "input_schema": input_schema,
            "return_message": return_message_obj if isinstance(return_message_obj, (str, Mapping)) else "",
            "llm_result_fields": llm_result_fields,
            "llm_result_schema": llm_result_schema,
            "metadata": metadata,
        }

        # 透传 llm_result_fields（来源：registry._extract_entries_preview）
        llm_fields_obj = normalized_preview.get("llm_result_fields")
        if isinstance(llm_fields_obj, list):
            entry_dict["llm_result_fields"] = llm_fields_obj

        entries.append(entry_dict)


def _append_plugin_fallback(
    *,
    result: list[dict[str, object]],
    plugin_id: str,
    plugin_meta_obj: object,
    exc: Exception,
) -> None:
    fallback_name = plugin_id
    fallback_description = ""
    if isinstance(plugin_meta_obj, Mapping):
        name_obj = plugin_meta_obj.get("name")
        description_obj = plugin_meta_obj.get("description")
        if isinstance(name_obj, str) and name_obj:
            fallback_name = name_obj
        if isinstance(description_obj, str):
            fallback_description = description_obj

    logger.warning(
        "error processing plugin metadata: plugin_id={}, err_type={}, err={}",
        plugin_id,
        type(exc).__name__,
        str(exc),
    )
    result.append(
        {
            "id": plugin_id,
            "name": fallback_name,
            "description": fallback_description,
            "entries": [],
        }
    )


def _build_plugin_list_sync() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    try:
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=2.0)
        if not plugins_snapshot:
            return result
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=2.0)
        handlers_snapshot = state.get_event_handlers_snapshot_cached(timeout=2.0)
    except IO_RUNTIME_ERRORS as exc:
        logger.warning(
            "failed to get state snapshots for plugin list: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )
        return result

    running_plugin_ids = set()
    for plugin_id, host_obj in hosts_snapshot.items():
        if not isinstance(plugin_id, str):
            continue
        try:
            if hasattr(host_obj, "is_alive") and host_obj.is_alive():
                running_plugin_ids.add(plugin_id)
        except Exception:
            pass

    for plugin_id_obj, plugin_meta_obj in plugins_snapshot.items():
        if not isinstance(plugin_id_obj, str):
            continue
        plugin_id = plugin_id_obj
        try:
            if not isinstance(plugin_meta_obj, Mapping):
                raise TypeError("plugin metadata is not a mapping")

            plugin_meta = _normalize_mapping(plugin_meta_obj, context=f"plugins[{plugin_id}]")
            plugin_info = dict(plugin_meta)
            plugin_info["status"] = _resolve_plugin_status(
                plugin_id=plugin_id,
                plugin_meta=plugin_meta,
                running_plugin_ids=running_plugin_ids,
            )

            entries, seen = _build_entries_from_handlers(
                plugin_id=plugin_id,
                handlers_snapshot=handlers_snapshot,
                plugin_meta=plugin_meta,
            )
            _append_entries_from_preview(
                plugin_id=plugin_id,
                plugin_meta=plugin_meta,
                entries=entries,
                seen=seen,
            )
            plugin_i18n = load_plugin_i18n_from_meta(plugin_meta)
            entries = [
                resolve_i18n_refs(entry, plugin_i18n, locale=_resolve_default_locale())  # type: ignore[misc]
                for entry in entries
                if isinstance(entry, dict)
            ]

            plugin_info["entries"] = entries
            plugin_info["list_actions"] = resolve_i18n_refs(
                _build_plugin_list_actions_from_meta(plugin_id, plugin_meta),
                plugin_i18n,
                locale=_resolve_default_locale(),
            )
            # Req 15 / design §11.2 — attach install_source sub-object.
            # Never raises; always produces either a real entry view or
            # _DEFAULT_INSTALL_SOURCE so /plugins stays 200.
            _attach_install_source(plugin_info, plugin_id, plugin_meta)
            result.append(plugin_info)
        except ServerDomainError as exc:
            _append_plugin_fallback(
                result=result,
                plugin_id=plugin_id,
                plugin_meta_obj=plugin_meta_obj,
                exc=exc,
            )
        except IO_RUNTIME_ERRORS as exc:
            _append_plugin_fallback(
                result=result,
                plugin_id=plugin_id,
                plugin_meta_obj=plugin_meta_obj,
                exc=exc,
            )

    return result


class PluginQueryService:
    async def get_plugin_status(self, plugin_id: str | None) -> dict[str, object]:
        try:
            if plugin_id is None:
                raw_status = await asyncio.to_thread(status_manager.get_plugin_status)
                if not isinstance(raw_status, Mapping):
                    raise ServerDomainError(
                        code="INVALID_DATA_SHAPE",
                        message="status manager returned non-object",
                        status_code=500,
                        details={"result_type": type(raw_status).__name__},
                    )
                return {
                    "plugins": _normalize_mapping(raw_status, context="plugin_status"),
                    "time": now_iso(),
                }

            raw_status = await asyncio.to_thread(status_manager.get_plugin_status, plugin_id)
            if not isinstance(raw_status, Mapping):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="status manager returned non-object",
                    status_code=500,
                    details={"plugin_id": plugin_id, "result_type": type(raw_status).__name__},
                )
            normalized = _normalize_mapping(raw_status, context=f"plugin_status[{plugin_id}]")
            if "time" not in normalized:
                normalized["time"] = now_iso()
            return normalized
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_plugin_status failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_STATUS_QUERY_FAILED",
                message="Failed to query plugin status",
                status_code=500,
                details={
                    "plugin_id": plugin_id or "",
                    "error_type": type(exc).__name__,
                },
            ) from exc

    async def list_plugins(self) -> dict[str, object]:
        try:
            raw_plugins = await asyncio.to_thread(_build_plugin_list_sync)
            if not isinstance(raw_plugins, list):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="plugin list result is not an array",
                    status_code=500,
                    details={"result_type": type(raw_plugins).__name__},
                )

            normalized_plugins = _normalize_plugin_entries(raw_plugins)
            return {
                "plugins": normalized_plugins,
                "message": "" if normalized_plugins else "no plugins registered",
            }
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "list_plugins failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_LIST_FAILED",
                message="Failed to list plugins",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

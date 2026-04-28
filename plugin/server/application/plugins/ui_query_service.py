from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from plugin.core.state import state
from plugin._types.models import PluginUiSurface, PluginUiWarning
from plugin.logging_config import get_logger
from plugin.core.ui_manifest import (
    default_permissions,
    normalize_warnings,
    resolve_surface_entry_path,
    static_surface_url,
)
from plugin.server.domain import IO_RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.sdk.shared.i18n import load_plugin_i18n_from_meta, resolve_i18n_refs

logger = get_logger("server.application.plugins.ui_query")
_ALLOWED_PLUGIN_LIST_ACTION_KINDS = {"builtin", "ui", "route", "url"}
_ALLOWED_PLUGIN_LIST_ACTION_OPEN_IN = {"new_tab", "same_tab"}
_ALLOWED_PLUGIN_LIST_ACTION_CONFIRM_MODES = {"dialog", "hold"}
_ALLOWED_PLUGIN_LIST_ACTION_BUILTINS = {
    "open_detail",
    "open_config",
    "open_logs",
    "open_panel",
    "open_guide",
    "start",
    "stop",
    "reload",
    "pack",
    "delete",
    "enable_extension",
    "disable_extension",
}
_HOSTED_TRANSLATIONS_MAX_BYTES = 128 * 1024
_HOSTED_TRANSLATIONS_DEFAULT_SOURCE_LOCALE = "en-US"


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


def _get_plugin_meta_sync(plugin_id: str) -> dict[str, object] | None:
    with state.acquire_plugins_read_lock():
        plugin_meta_obj = state.plugins.get(plugin_id)
    if not isinstance(plugin_meta_obj, Mapping):
        return None
    return _normalize_mapping(plugin_meta_obj, context=f"plugins[{plugin_id}]")


def _get_static_ui_config_from_meta(plugin_meta: Mapping[str, object]) -> dict[str, object] | None:
    static_ui_obj = plugin_meta.get("static_ui_config")
    if not isinstance(static_ui_obj, Mapping):
        return _infer_static_ui_config_from_meta(plugin_meta)
    return _normalize_mapping(static_ui_obj, context="plugins.static_ui_config")


def _get_plugin_ui_config_from_meta(plugin_meta: Mapping[str, object]) -> dict[str, object] | None:
    plugin_ui_obj = plugin_meta.get("plugin_ui")
    if not isinstance(plugin_ui_obj, Mapping):
        plugin_ui_obj = plugin_meta.get("ui")
    if not isinstance(plugin_ui_obj, Mapping):
        return None
    return _normalize_mapping(plugin_ui_obj, context="plugins.plugin_ui")


def _infer_static_ui_config_from_meta(plugin_meta: Mapping[str, object]) -> dict[str, object] | None:
    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None

    try:
        config_path = Path(config_path_obj)
    except Exception:
        return None

    static_dir = config_path.parent / "static"
    index_file = static_dir / "index.html"
    if not static_dir.is_dir() or not index_file.is_file():
        return None

    plugin_id_obj = plugin_meta.get("id") or plugin_meta.get("plugin_id")
    plugin_id = str(plugin_id_obj) if plugin_id_obj is not None else ""
    return {
        "enabled": True,
        "directory": str(static_dir),
        "index_file": "index.html",
        "cache_control": "public, max-age=3600",
        "plugin_id": plugin_id,
        "inferred": True,
    }


def _resolve_static_dir(static_ui_config: Mapping[str, object]) -> Path | None:
    enabled = _to_bool(static_ui_config.get("enabled"), default=False)
    if not enabled:
        return None

    directory_obj = static_ui_config.get("directory")
    if not isinstance(directory_obj, str) or not directory_obj:
        return None

    static_dir = Path(directory_obj)
    if static_dir.exists() and static_dir.is_dir():
        return static_dir
    return None


def _list_static_files_sync(static_dir: Path) -> list[str]:
    static_files: list[str] = []
    for file_path in static_dir.rglob("*"):
        if not file_path.is_file():
            continue
        rel_path = file_path.relative_to(static_dir)
        static_files.append(str(rel_path))
    return static_files


def _has_static_ui_from_meta(plugin_meta: Mapping[str, object]) -> bool:
    static_ui_config = _get_static_ui_config_from_meta(plugin_meta)
    if static_ui_config is None:
        return False
    static_dir = _resolve_static_dir(static_ui_config)
    return static_dir is not None and (static_dir / "index.html").exists()


def _surface_from_mapping(
    raw_surface: object,
    *,
    plugin_id: str,
    plugin_meta: Mapping[str, object],
    kind: str,
    index: int,
) -> dict[str, object] | None:
    if not isinstance(raw_surface, Mapping):
        return None
    surface = _normalize_mapping(raw_surface, context=f"plugins.plugin_ui.{kind}[{index}]")
    surface_id_obj = surface.get("id")
    surface_id = surface_id_obj.strip() if isinstance(surface_id_obj, str) and surface_id_obj.strip() else "main"
    mode_obj = surface.get("mode")
    mode = mode_obj.strip().lower() if isinstance(mode_obj, str) and mode_obj.strip() else "static"
    entry_obj = surface.get("entry")
    entry = entry_obj.strip() if isinstance(entry_obj, str) and entry_obj.strip() else ""
    if not entry and mode != "auto":
        return None
    title_obj = surface.get("title")
    open_in_obj = surface.get("open_in")
    open_in = open_in_obj.strip().lower() if isinstance(open_in_obj, str) and open_in_obj.strip() else "iframe"
    permissions_obj = surface.get("permissions")
    permissions = [item for item in permissions_obj if isinstance(item, str)] if isinstance(permissions_obj, list) else default_permissions(kind)
    available = True
    warnings: list[dict[str, object]] = []
    if entry and mode in {"static", "hosted-tsx", "markdown"}:
        entry_path = resolve_surface_entry_path(plugin_meta, entry)
        available = entry_path is not None and entry_path.is_file()
        if not available:
            warnings.append(PluginUiWarning(
                path=f"plugin.ui.{kind}[{index}].entry",
                code="entry_not_found",
                message=f"Entry file '{entry}' was not found under the plugin directory.",
            ).model_dump())
    normalized = PluginUiSurface(
        id=surface_id,
        kind="docs" if kind == "docs" else kind,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        title=title_obj.strip() if isinstance(title_obj, str) and title_obj.strip() else None,
        entry=entry or None,
        url=static_surface_url(plugin_id, mode, entry) if entry else None,
        ui_path=static_surface_url(plugin_id, mode, entry) if entry else None,
        open_in=open_in,  # type: ignore[arg-type]
        context=surface.get("context") if isinstance(surface.get("context"), str) else None,
        permissions=permissions,
        available=available,
    ).model_dump(exclude_none=True)
    if warnings:
        normalized["_warnings"] = warnings
    return normalized


def _build_manifest_surfaces(plugin_id: str, plugin_meta: Mapping[str, object]) -> list[dict[str, object]]:
    plugin_ui = _get_plugin_ui_config_from_meta(plugin_meta)
    if not plugin_ui or not _to_bool(plugin_ui.get("enabled"), default=True):
        return []

    surfaces: list[dict[str, object]] = []
    for kind in ("panel", "guide", "docs"):
        raw_list = plugin_ui.get(kind)
        if not isinstance(raw_list, list):
            continue
        for index, raw_surface in enumerate(raw_list):
            normalized = _surface_from_mapping(
                raw_surface,
                plugin_id=plugin_id,
                plugin_meta=plugin_meta,
                kind=kind,
                index=index,
            )
            if normalized is not None:
                surfaces.append(normalized)
    return surfaces


def _build_static_compat_surface(plugin_id: str, plugin_meta: Mapping[str, object]) -> dict[str, object] | None:
    static_ui_config = _get_static_ui_config_from_meta(plugin_meta)
    if static_ui_config is None:
        return None
    static_dir = _resolve_static_dir(static_ui_config)
    if static_dir is None or not (static_dir / "index.html").exists():
        return None
    return PluginUiSurface(
        id="main",
        kind="panel",
        mode="static",
        title=None,
        entry="static/index.html",
        url=f"/plugin/{plugin_id}/ui/",
        ui_path=f"/plugin/{plugin_id}/ui/",
        open_in="iframe",
        permissions=["state:read"],
        available=True,
    ).model_dump(exclude_none=True)


def _build_surfaces_sync(plugin_id: str, plugin_meta: Mapping[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    surfaces = _build_manifest_surfaces(plugin_id, plugin_meta)
    warnings = normalize_warnings(plugin_meta.get("plugin_ui", {}).get("warnings") if isinstance(plugin_meta.get("plugin_ui"), Mapping) else None)
    for surface in surfaces:
        surface_warnings = surface.pop("_warnings", None)
        warnings.extend(normalize_warnings(surface_warnings))
    seen = {(str(surface.get("kind")), str(surface.get("id"))) for surface in surfaces}
    static_surface = _build_static_compat_surface(plugin_id, plugin_meta)
    if static_surface is not None and ("panel", "main") not in seen:
        surfaces.insert(0, static_surface)
    return surfaces, warnings


def _find_surface(surfaces: list[dict[str, object]], *, kind: str, surface_id: str) -> dict[str, object] | None:
    return next(
        (
            surface for surface in surfaces
            if surface.get("kind") == kind and surface.get("id") == surface_id
        ),
        None,
    )


def _surface_context_id(surface: Mapping[str, object]) -> str:
    context_id = surface.get("context")
    if isinstance(context_id, str) and context_id.strip():
        return context_id.strip()
    surface_id = surface.get("id")
    if isinstance(surface_id, str) and surface_id.strip():
        return surface_id.strip()
    return "main"


def _surface_allows_action_call(surface: Mapping[str, object]) -> bool:
    permissions = surface.get("permissions")
    return isinstance(permissions, list) and "action:call" in permissions


def _normalize_translations(raw: object, *, path: Path) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ServerDomainError(
            code="PLUGIN_UI_TRANSLATIONS_INVALID",
            message=f"Hosted UI translations file '{path.name}' must be a JSON object",
            status_code=500,
            details={"path": str(path)},
        )
    source_locale = raw.get("sourceLocale")
    if not isinstance(source_locale, str) or not source_locale.strip():
        source_locale = _HOSTED_TRANSLATIONS_DEFAULT_SOURCE_LOCALE
    translations_obj = raw.get("translations", {})
    if not isinstance(translations_obj, Mapping):
        raise ServerDomainError(
            code="PLUGIN_UI_TRANSLATIONS_INVALID",
            message=f"Hosted UI translations file '{path.name}' must define translations as an object",
            status_code=500,
            details={"path": str(path)},
        )
    translations: dict[str, dict[str, str]] = {}
    for locale, messages_obj in translations_obj.items():
        if not isinstance(locale, str) or not locale.strip() or not isinstance(messages_obj, Mapping):
            raise ServerDomainError(
                code="PLUGIN_UI_TRANSLATIONS_INVALID",
                message=f"Hosted UI translations file '{path.name}' contains invalid locale entries",
                status_code=500,
                details={"path": str(path), "locale": str(locale)},
            )
        messages: dict[str, str] = {}
        for key, value in messages_obj.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ServerDomainError(
                    code="PLUGIN_UI_TRANSLATIONS_INVALID",
                    message=f"Hosted UI translations file '{path.name}' messages must be string-to-string mappings",
                    status_code=500,
                    details={"path": str(path), "locale": locale, "key": str(key)},
                )
            messages[key] = value
        translations[locale] = messages
    return {
        "source_locale": source_locale.strip(),
        "translations": translations,
    }


def _load_surface_translations_sync(entry_path: Path) -> dict[str, object]:
    translations_path = entry_path.with_suffix(".translations.json")
    if not translations_path.is_file():
        return {
            "source_locale": _HOSTED_TRANSLATIONS_DEFAULT_SOURCE_LOCALE,
            "translations": {},
        }
    try:
        if translations_path.stat().st_size > _HOSTED_TRANSLATIONS_MAX_BYTES:
            raise ServerDomainError(
                code="PLUGIN_UI_TRANSLATIONS_TOO_LARGE",
                message=f"Hosted UI translations file '{translations_path.name}' is too large",
                status_code=500,
                details={"path": str(translations_path), "max_bytes": _HOSTED_TRANSLATIONS_MAX_BYTES},
            )
        raw = json.loads(translations_path.read_text(encoding="utf-8"))
    except ServerDomainError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ServerDomainError(
            code="PLUGIN_UI_TRANSLATIONS_READ_FAILED",
            message=f"Failed to read hosted UI translations file '{translations_path.name}'",
            status_code=500,
            details={"path": str(translations_path), "error_type": type(exc).__name__},
        ) from exc
    return _normalize_translations(raw, path=translations_path)


def _entry_ids_from_meta(plugin_meta: Mapping[str, object]) -> set[str]:
    entries_obj = plugin_meta.get("entries")
    if not isinstance(entries_obj, list):
        entries_obj = plugin_meta.get("entries_preview")
    return {
        str(item.get("id"))
        for item in entries_obj
        if isinstance(item, Mapping) and item.get("id")
    } if isinstance(entries_obj, list) else set()


def _resolve_authorized_action_entry_id(
    action_id: str,
    *,
    actions: list[object],
    entry_ids: set[str],
) -> str | None:
    for raw_action in actions:
        if not isinstance(raw_action, Mapping):
            continue
        exposed_id_obj = raw_action.get("id")
        entry_id_obj = raw_action.get("entry_id")
        exposed_id = str(exposed_id_obj) if exposed_id_obj else ""
        entry_id = str(entry_id_obj) if entry_id_obj else ""
        if action_id not in {exposed_id, entry_id}:
            continue
        if entry_id and entry_id in entry_ids:
            return entry_id
        if exposed_id and exposed_id in entry_ids:
            return exposed_id
    return None


def _add_surface_route_actions(
    actions: list[dict[str, object]],
    seen_ids: set[str],
    *,
    plugin_id: str,
    plugin_meta: Mapping[str, object],
) -> None:
    surfaces, _warnings = _build_surfaces_sync(plugin_id, plugin_meta)
    has_panel = any(surface.get("kind") == "panel" and surface.get("available") is not False for surface in surfaces)
    has_guide = any(surface.get("kind") in {"guide", "docs"} and surface.get("available") is not False for surface in surfaces)
    safe_id = plugin_id.replace("/", "%2F")
    if has_panel and "open_panel" not in seen_ids:
        actions.append({
            "id": "open_panel",
            "kind": "route",
            "target": f"/plugins/{safe_id}?tab=panel",
        })
        seen_ids.add("open_panel")
    if has_guide and "open_guide" not in seen_ids:
        actions.append({
            "id": "open_guide",
            "kind": "route",
            "target": f"/plugins/{safe_id}?tab=guide",
        })
        seen_ids.add("open_guide")


def _normalize_plugin_list_action(
    raw_action: object,
    *,
    plugin_id: str,
    context: str,
) -> dict[str, object] | None:
    if not isinstance(raw_action, Mapping):
        return None

    action = _normalize_mapping(raw_action, context=context)
    action_id_obj = action.get("id")
    if isinstance(action_id_obj, str) and action_id_obj.strip():
        action_id = action_id_obj.strip()
    else:
        return None

    kind_obj = action.get("kind")
    kind = str(kind_obj).strip().lower() if isinstance(kind_obj, str) and kind_obj.strip() else "builtin"
    if kind not in _ALLOWED_PLUGIN_LIST_ACTION_KINDS:
        return None
    if kind == "builtin" and action_id not in _ALLOWED_PLUGIN_LIST_ACTION_BUILTINS:
        return None

    normalized: dict[str, object] = {
        "id": action_id,
        "kind": kind,
    }

    label_obj = action.get("label")
    if isinstance(label_obj, str) and label_obj.strip():
        normalized["label"] = label_obj.strip()
    elif isinstance(label_obj, Mapping):
        normalized["label"] = dict(label_obj)

    target_obj = action.get("target")
    if isinstance(target_obj, str) and target_obj.strip():
        normalized["target"] = target_obj.strip().replace("{plugin_id}", plugin_id)

    icon_obj = action.get("icon")
    if isinstance(icon_obj, str) and icon_obj.strip():
        normalized["icon"] = icon_obj.strip()

    confirm_message_obj = action.get("confirm_message")
    if isinstance(confirm_message_obj, str) and confirm_message_obj.strip():
        normalized["confirm_message"] = confirm_message_obj.strip()
    elif isinstance(confirm_message_obj, Mapping):
        normalized["confirm_message"] = dict(confirm_message_obj)

    confirm_mode_obj = action.get("confirm_mode")
    if isinstance(confirm_mode_obj, str) and confirm_mode_obj in _ALLOWED_PLUGIN_LIST_ACTION_CONFIRM_MODES:
        normalized["confirm_mode"] = confirm_mode_obj

    if isinstance(action.get("danger"), bool):
        normalized["danger"] = action["danger"]
    if isinstance(action.get("disabled"), bool):
        normalized["disabled"] = action["disabled"]
    if isinstance(action.get("requires_running"), bool):
        normalized["requires_running"] = action["requires_running"]

    open_in_obj = action.get("open_in")
    if isinstance(open_in_obj, str) and open_in_obj in _ALLOWED_PLUGIN_LIST_ACTION_OPEN_IN:
        normalized["open_in"] = open_in_obj

    return normalized


def _build_plugin_list_actions_from_meta(
    plugin_id: str,
    plugin_meta: Mapping[str, object],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    seen_ids: set[str] = set()

    raw_actions = plugin_meta.get("list_actions")
    if isinstance(raw_actions, list):
        for index, raw_action in enumerate(raw_actions):
            normalized = _normalize_plugin_list_action(
                raw_action,
                plugin_id=plugin_id,
                context=f"plugins[{plugin_id}].list_actions[{index}]",
            )
            if normalized is None:
                continue
            action_id = str(normalized["id"])
            if action_id in seen_ids:
                continue
            actions.append(normalized)
            seen_ids.add(action_id)

    _add_surface_route_actions(actions, seen_ids, plugin_id=plugin_id, plugin_meta=plugin_meta)

    return actions


class PluginUiQueryService:
    async def get_surfaces(self, plugin_id: str) -> dict[str, object]:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            surfaces, warnings = _build_surfaces_sync(plugin_id, plugin_meta)

            return {
                "plugin_id": plugin_id,
                "surfaces": surfaces,
                "warnings": warnings,
            }
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_surfaces failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_QUERY_FAILED",
                message="Failed to query plugin UI surfaces",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_surface_source(self, plugin_id: str, *, kind: str, surface_id: str) -> dict[str, object]:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            surfaces, warnings = _build_surfaces_sync(plugin_id, plugin_meta)
            surface = next(
                (
                    item for item in surfaces
                    if item.get("kind") == kind and item.get("id") == surface_id
                ),
                None,
            )
            if surface is None:
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_NOT_FOUND",
                    message=f"UI surface '{kind}:{surface_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id},
                )

            mode = str(surface.get("mode") or "")
            if mode not in {"hosted-tsx", "markdown"}:
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_SOURCE_UNAVAILABLE",
                    message=f"UI surface '{kind}:{surface_id}' does not expose source",
                    status_code=400,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id, "mode": mode},
                )

            entry_obj = surface.get("entry")
            if not isinstance(entry_obj, str) or not entry_obj:
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_ENTRY_MISSING",
                    message=f"UI surface '{kind}:{surface_id}' has no entry",
                    status_code=400,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id},
                )

            entry_path = resolve_surface_entry_path(plugin_meta, entry_obj)
            if entry_path is None or not entry_path.is_file():
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_ENTRY_NOT_FOUND",
                    message=f"UI surface entry '{entry_obj}' was not found",
                    status_code=404,
                    details={"plugin_id": plugin_id, "entry": entry_obj},
                )

            source = await asyncio.to_thread(entry_path.read_text, encoding="utf-8")
            translations_payload = await asyncio.to_thread(_load_surface_translations_sync, entry_path)
            return {
                "plugin_id": plugin_id,
                "kind": kind,
                "surface_id": surface_id,
                "mode": mode,
                "entry": entry_obj,
                "source": source,
                "source_locale": translations_payload["source_locale"],
                "translations": translations_payload["translations"],
                "warnings": warnings,
            }
        except ServerDomainError:
            raise
        except (OSError, UnicodeError) as exc:
            logger.error(
                "get_surface_source failed: plugin_id={}, kind={}, surface_id={}, err_type={}, err={}",
                plugin_id,
                kind,
                surface_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_SOURCE_READ_FAILED",
                message="Failed to read plugin UI source",
                status_code=500,
                details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_surface_context(self, plugin_id: str, *, kind: str, surface_id: str, locale: str | None = None) -> dict[str, object]:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            surfaces, warnings = _build_surfaces_sync(plugin_id, plugin_meta)
            surface = next(
                (
                    item for item in surfaces
                    if item.get("kind") == kind and item.get("id") == surface_id
                ),
                None,
            )
            if surface is None:
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_NOT_FOUND",
                    message=f"UI surface '{kind}:{surface_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id},
                )

            entries_obj = plugin_meta.get("entries")
            if not isinstance(entries_obj, list):
                entries_obj = plugin_meta.get("entries_preview")
            entries = [dict(item) for item in entries_obj if isinstance(item, Mapping)] if isinstance(entries_obj, list) else []

            actions_obj = plugin_meta.get("list_actions")
            actions = [dict(item) for item in actions_obj if isinstance(item, Mapping)] if isinstance(actions_obj, list) else []

            config_schema = plugin_meta.get("input_schema") if isinstance(plugin_meta.get("input_schema"), Mapping) else {"type": "object", "properties": {}}
            state_payload: object = {}
            state_schema: object = None
            context_id = surface.get("context")
            if not isinstance(context_id, str) or not context_id.strip():
                context_id = str(surface.get("id") or "main")
            with state.acquire_plugin_hosts_read_lock():
                host = state.plugin_hosts.get(plugin_id)
            if host is not None and hasattr(host, "is_alive") and host.is_alive() and hasattr(host, "get_ui_context"):
                try:
                    ui_context_result = await host.get_ui_context(str(context_id))
                    if isinstance(ui_context_result, Mapping):
                        state_payload = ui_context_result.get("state", {})
                        state_schema = ui_context_result.get("state_schema")
                        context_actions = ui_context_result.get("actions")
                        if isinstance(context_actions, list):
                            actions = [
                                dict(item)
                                for item in context_actions
                                if isinstance(item, Mapping)
                            ]
                except Exception as exc:
                    warnings.append(PluginUiWarning(
                        path=f"plugin.ui.{kind}.{surface_id}.context",
                        code="ui_context_failed",
                        message=f"Failed to load UI context '{context_id}': {exc}",
                    ).model_dump())

            plugin_i18n = load_plugin_i18n_from_meta(plugin_meta)
            resolved_locale = locale or "en"
            resolved_context = {
                "plugin_id": plugin_id,
                "kind": kind,
                "surface_id": surface_id,
                "plugin": dict(plugin_meta),
                "surface": surface,
                "state": state_payload,
                "state_schema": state_schema,
                "actions": actions,
                "entries": entries,
                "config": {
                    "schema": config_schema,
                    "value": {},
                    "readonly": True,
                },
                "warnings": warnings,
                "i18n": {
                    "locale": resolved_locale,
                    "messages": plugin_i18n.messages,
                    "default_locale": plugin_i18n.default_locale,
                },
            }
            return resolve_i18n_refs(resolved_context, plugin_i18n, locale=resolved_locale)  # type: ignore[return-value]
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_surface_context failed: plugin_id={}, kind={}, surface_id={}, err_type={}, err={}",
                plugin_id,
                kind,
                surface_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_CONTEXT_QUERY_FAILED",
                message="Failed to query plugin UI context",
                status_code=500,
                details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id, "error_type": type(exc).__name__},
            ) from exc

    async def call_surface_action(
        self,
        plugin_id: str,
        *,
        action_id: str,
        args: Mapping[str, object] | None,
        kind: str = "panel",
        surface_id: str = "main",
    ) -> dict[str, object]:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            surfaces, _warnings = _build_surfaces_sync(plugin_id, plugin_meta)
            surface = _find_surface(surfaces, kind=kind, surface_id=surface_id)
            if surface is None:
                logger.warning(
                    "Hosted UI action rejected: plugin_id={}, surface={}:{}, action_id={}, reason=surface_not_found",
                    plugin_id, kind, surface_id, action_id,
                )
                raise ServerDomainError(
                    code="PLUGIN_UI_SURFACE_NOT_FOUND",
                    message=f"UI surface '{kind}:{surface_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id},
                )
            if not _surface_allows_action_call(surface):
                logger.warning(
                    "Hosted UI action rejected: plugin_id={}, surface={}:{}, action_id={}, reason=missing_action_permission",
                    plugin_id, kind, surface_id, action_id,
                )
                raise ServerDomainError(
                    code="PLUGIN_UI_ACTION_FORBIDDEN",
                    message=f"UI surface '{kind}:{surface_id}' is not allowed to call plugin actions",
                    status_code=403,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id, "action_id": action_id},
                )

            entry_ids = _entry_ids_from_meta(plugin_meta)
            if not entry_ids:
                logger.warning(
                    "Hosted UI action rejected: plugin_id={}, surface={}:{}, action_id={}, reason=no_plugin_entries",
                    plugin_id, kind, surface_id, action_id,
                )
                raise ServerDomainError(
                    code="PLUGIN_UI_ACTION_NOT_FOUND",
                    message=f"UI action '{action_id}' is not a plugin entry",
                    status_code=404,
                    details={"plugin_id": plugin_id, "action_id": action_id},
                )
            with state.acquire_plugin_hosts_read_lock():
                host = state.plugin_hosts.get(plugin_id)
            if host is None or not hasattr(host, "is_alive") or not host.is_alive() or not hasattr(host, "trigger"):
                logger.warning(
                    "Hosted UI action rejected: plugin_id={}, surface={}:{}, action_id={}, reason=plugin_not_running",
                    plugin_id, kind, surface_id, action_id,
                )
                raise ServerDomainError(
                    code="PLUGIN_NOT_RUNNING",
                    message=f"Plugin '{plugin_id}' is not running",
                    status_code=409,
                    details={"plugin_id": plugin_id},
                )

            actions: list[object] = []
            if hasattr(host, "get_ui_context"):
                try:
                    ui_context_result = await host.get_ui_context(_surface_context_id(surface))
                    if isinstance(ui_context_result, Mapping) and isinstance(ui_context_result.get("actions"), list):
                        actions = list(ui_context_result["actions"])
                except Exception as exc:
                    logger.warning(
                        "Hosted UI action context failed: plugin_id={}, surface={}:{}, action_id={}, err_type={}, err={}",
                        plugin_id, kind, surface_id, action_id, type(exc).__name__, str(exc),
                    )
                    raise ServerDomainError(
                        code="PLUGIN_UI_CONTEXT_QUERY_FAILED",
                        message="Failed to query plugin UI action context",
                        status_code=500,
                        details={
                            "plugin_id": plugin_id,
                            "kind": kind,
                            "surface_id": surface_id,
                            "action_id": action_id,
                            "error_type": type(exc).__name__,
                        },
                    ) from exc

            resolved_action_id = _resolve_authorized_action_entry_id(
                action_id,
                actions=actions,
                entry_ids=entry_ids,
            )
            if resolved_action_id is None:
                logger.warning(
                    "Hosted UI action rejected: plugin_id={}, surface={}:{}, action_id={}, reason=action_not_exposed",
                    plugin_id, kind, surface_id, action_id,
                )
                raise ServerDomainError(
                    code="PLUGIN_UI_ACTION_FORBIDDEN",
                    message=f"UI action '{action_id}' is not exposed by surface '{kind}:{surface_id}'",
                    status_code=403,
                    details={"plugin_id": plugin_id, "kind": kind, "surface_id": surface_id, "action_id": action_id},
                )

            result = await host.trigger(resolved_action_id, dict(args or {}))
            return {
                "plugin_id": plugin_id,
                "action_id": resolved_action_id,
                "result": result,
            }
        except ServerDomainError:
            raise
        except Exception as exc:
            logger.error(
                "call_surface_action failed: plugin_id={}, surface={}:{}, action_id={}, err_type={}, err={}",
                plugin_id,
                kind,
                surface_id,
                action_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_ACTION_FAILED",
                message="Failed to execute plugin UI action",
                status_code=500,
                details={"plugin_id": plugin_id, "action_id": action_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_static_dir(self, plugin_id: str) -> Path | None:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                return None
            static_ui_config = _get_static_ui_config_from_meta(plugin_meta)
            if static_ui_config is None:
                return None
            return _resolve_static_dir(static_ui_config)
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_static_dir failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_QUERY_FAILED",
                message="Failed to query plugin static directory",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_static_ui_config(self, plugin_id: str) -> dict[str, object] | None:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                return None
            return _get_static_ui_config_from_meta(plugin_meta)
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_static_ui_config failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_QUERY_FAILED",
                message="Failed to query plugin static UI config",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

    async def get_ui_info(self, plugin_id: str) -> dict[str, object]:
        try:
            plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
            if plugin_meta is None:
                raise ServerDomainError(
                    code="PLUGIN_NOT_FOUND",
                    message=f"Plugin '{plugin_id}' not found",
                    status_code=404,
                    details={"plugin_id": plugin_id},
                )

            static_ui_config = _get_static_ui_config_from_meta(plugin_meta)
            static_dir = _resolve_static_dir(static_ui_config) if static_ui_config is not None else None
            has_ui = static_dir is not None and (static_dir / "index.html").exists()

            static_files: list[str] = []
            if static_dir is not None and static_dir.exists():
                static_files = await asyncio.to_thread(_list_static_files_sync, static_dir)

            explicitly_registered = (
                static_ui_config is not None
                and _to_bool(static_ui_config.get("enabled"), default=False)
            )

            return {
                "plugin_id": plugin_id,
                "has_ui": has_ui,
                "explicitly_registered": explicitly_registered,
                "ui_path": f"/plugin/{plugin_id}/ui/" if has_ui else None,
                "static_dir": str(static_dir) if static_dir is not None else None,
                "static_files": static_files[:50],
                "static_files_count": len(static_files),
            }
        except ServerDomainError:
            raise
        except IO_RUNTIME_ERRORS as exc:
            logger.error(
                "get_ui_info failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_UI_QUERY_FAILED",
                message="Failed to query plugin UI info",
                status_code=500,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc

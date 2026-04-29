from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from plugin._types.models import PluginUiSurface, PluginUiWarning
from plugin.utils import parse_bool_config

SURFACE_KINDS = {"panel", "guide", "docs"}
SURFACE_MODES = {"static", "hosted-tsx", "markdown", "auto"}
OPEN_IN_VALUES = {"iframe", "new_tab", "same_tab"}
PERMISSIONS = {"state:read", "config:read", "config:write", "action:call", "logs:read", "runs:read"}


def default_permissions(kind: str) -> list[str]:
    if kind in {"guide", "docs"}:
        return ["state:read"]
    return ["state:read", "config:read", "action:call"]


def normalize_warnings(raw_warnings: object) -> list[dict[str, object]]:
    if not isinstance(raw_warnings, list):
        return []

    warnings: list[dict[str, object]] = []
    for index, raw_warning in enumerate(raw_warnings):
        if not isinstance(raw_warning, Mapping):
            continue
        path = raw_warning.get("path")
        code = raw_warning.get("code")
        message = raw_warning.get("message")
        warnings.append(
            PluginUiWarning(
                path=path if isinstance(path, str) and path else f"plugin.ui.warnings[{index}]",
                code=code if isinstance(code, str) and code else "ui_manifest_warning",
                message=message if isinstance(message, str) and message else "UI manifest warning",
            ).model_dump()
        )
    return warnings


def normalize_plugin_ui_manifest(conf: Mapping[str, Any], *, plugin_id: str = "") -> dict[str, Any] | None:
    plugin_section = conf.get("plugin")
    if not isinstance(plugin_section, Mapping):
        return None

    ui_section = plugin_section.get("ui")
    if not isinstance(ui_section, Mapping):
        if ui_section is None:
            return None
        return {
            "enabled": False,
            "warnings": [{
                "path": "plugin.ui",
                "code": "invalid_ui_shape",
                "message": f"plugin.ui must be a table, got {type(ui_section).__name__}.",
            }],
        }

    result: dict[str, Any] = {
        "enabled": parse_bool_config(ui_section.get("enabled"), default=True),
    }
    warnings: list[dict[str, str]] = []

    for kind in ("panel", "guide", "docs"):
        raw_items = ui_section.get(kind)
        if raw_items is None:
            continue
        if isinstance(raw_items, Mapping):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            warnings.append({
                "path": f"plugin.ui.{kind}",
                "code": "invalid_surface_list",
                "message": f"Expected array of tables for {kind}, got {type(raw_items).__name__}.",
            })
            continue

        items: list[dict[str, Any]] = []
        for index, raw_surface in enumerate(raw_items):
            normalized, surface_warnings = normalize_manifest_surface(
                raw_surface,
                kind=kind,
                index=index,
                plugin_id=plugin_id,
            )
            warnings.extend(surface_warnings)
            if normalized is not None:
                items.append(normalized)
        if items:
            result[kind] = items

    if warnings:
        result["warnings"] = warnings
    return result


def normalize_manifest_surface(
    raw_surface: object,
    *,
    kind: str,
    index: int,
    plugin_id: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    path = f"plugin.ui.{kind}[{index}]"
    warnings: list[dict[str, str]] = []

    def add_warning(field: str, code: str, message: str) -> None:
        warnings.append({
            "path": f"{path}.{field}" if field else path,
            "code": code,
            "message": message,
        })

    if not isinstance(raw_surface, Mapping):
        return None, [{
            "path": path,
            "code": "invalid_surface_shape",
            "message": f"Surface item must be a table, got {type(raw_surface).__name__}.",
        }]

    entry = raw_surface.get("entry")
    inferred_id = "main"
    if isinstance(entry, str) and entry.strip():
        stem = Path(entry.strip()).stem.strip()
        if stem and stem not in {"index", "panel", "main"}:
            inferred_id = stem
    surface_id = raw_surface.get("id")
    if not isinstance(surface_id, str) or not surface_id.strip():
        if surface_id is not None:
            add_warning("id", "invalid_id", f"Surface id must be a non-empty string; using '{inferred_id}'.")
        surface_id = inferred_id

    mode = raw_surface.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        if mode is not None:
            add_warning("mode", "invalid_mode", "Surface mode must be a string; inferring from entry.")
        mode = infer_mode_from_entry(entry)
    elif mode.strip().lower() not in SURFACE_MODES:
        add_warning(
            "mode",
            "unsupported_mode",
            f"Unsupported mode '{mode}'. Use static, hosted-tsx, markdown, or auto; using static.",
        )
        mode = "static"
    mode = mode.strip().lower()

    if mode != "auto" and (not isinstance(entry, str) or not entry.strip()):
        return None, [{
            "path": f"{path}.entry",
            "code": "missing_entry",
            "message": f"Surface '{surface_id}' must define entry when mode is {mode}.",
        }]

    open_in = raw_surface.get("open_in")
    if isinstance(open_in, str) and open_in.strip().lower() in OPEN_IN_VALUES:
        open_in_value: str | None = open_in.strip().lower()
    else:
        if open_in is not None:
            add_warning("open_in", "invalid_open_in", "open_in must be iframe, new_tab, or same_tab; using default.")
        open_in_value = "iframe" if mode == "static" else None

    permissions = raw_surface.get("permissions")
    if isinstance(permissions, list):
        normalized_permissions: list[str] = []
        for perm_index, item in enumerate(permissions):
            if not isinstance(item, str) or not item.strip():
                add_warning(f"permissions[{perm_index}]", "invalid_permission", "Permission must be a non-empty string.")
                continue
            permission = item.strip()
            if permission not in PERMISSIONS:
                add_warning(f"permissions[{perm_index}]", "unknown_permission", f"Unknown permission '{permission}'.")
                continue
            normalized_permissions.append(permission)
    elif permissions is None:
        normalized_permissions = default_permissions(kind)
    else:
        add_warning("permissions", "invalid_permissions", "permissions must be an array of strings; using no permissions.")
        normalized_permissions = []

    normalized: dict[str, Any] = {
        "id": surface_id.strip(),
        "kind": kind,
        "mode": mode,
        "permissions": normalized_permissions,
    }
    if isinstance(raw_surface.get("title"), str) and raw_surface["title"].strip():
        normalized["title"] = raw_surface["title"].strip()
    if isinstance(entry, str) and entry.strip():
        normalized["entry"] = entry.strip()
    if open_in_value:
        normalized["open_in"] = open_in_value
    if isinstance(raw_surface.get("context"), str) and raw_surface["context"].strip():
        normalized["context"] = raw_surface["context"].strip()
    elif kind in {"panel", "guide"}:
        normalized["context"] = surface_id.strip()
    return normalized, warnings


def infer_mode_from_entry(entry: object) -> str:
    if not isinstance(entry, str) or not entry.strip():
        return "auto"
    suffix = Path(entry.strip()).suffix.lower()
    if suffix in {".tsx", ".jsx"}:
        return "hosted-tsx"
    if suffix in {".md", ".mdx"}:
        return "markdown"
    if suffix in {".html", ".htm"}:
        return "static"
    return "static"


def resolve_surface_entry_path(plugin_meta: Mapping[str, object], entry: str) -> Path | None:
    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None
    try:
        root = Path(config_path_obj).parent.resolve()
        candidate = (root / entry).resolve()
        candidate.relative_to(root)
        return candidate
    except Exception:
        return None


def static_surface_url(plugin_id: str, mode: str, entry: str) -> str | None:
    if mode != "static":
        return None
    normalized_entry = entry.strip().replace("\\", "/").lstrip("/")
    if normalized_entry == "static/index.html":
        return f"/plugin/{plugin_id}/ui/"
    if normalized_entry.startswith("static/"):
        rel = normalized_entry.removeprefix("static/")
        return f"/plugin/{plugin_id}/ui/{rel}"
    return None

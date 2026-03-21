from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.plugins.ui_query")


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


class PluginUiQueryService:
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

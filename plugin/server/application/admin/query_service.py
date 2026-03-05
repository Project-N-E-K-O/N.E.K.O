from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from plugin._types.version import SDK_VERSION
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso

logger = get_logger("server.application.admin.query")
_RUNTIME_ERRORS = (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError, TimeoutError)


def _available_snapshot_sync() -> int:
    plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
    return len(plugins_snapshot)


def _build_server_info_sync() -> dict[str, object]:
    plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
    hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)

    running_plugins_status: dict[str, dict[str, object]] = {}
    for plugin_id_obj, host_obj in hosts_snapshot.items():
        if not isinstance(plugin_id_obj, str):
            continue
        if host_obj is None:
            continue

        pid: int | None = None
        process_obj = getattr(host_obj, "process", None)
        if process_obj is not None:
            pid_obj = getattr(process_obj, "pid", None)
            if isinstance(pid_obj, int):
                pid = pid_obj

        alive = False
        host_is_alive = getattr(host_obj, "is_alive", None)
        if callable(host_is_alive):
            try:
                alive = bool(host_is_alive())
            except _RUNTIME_ERRORS:
                alive = False
        elif process_obj is not None:
            process_is_alive = getattr(process_obj, "is_alive", None)
            if callable(process_is_alive):
                try:
                    alive = bool(process_is_alive())
                except _RUNTIME_ERRORS:
                    alive = False

        running_plugins_status[plugin_id_obj] = {
            "alive": alive,
            "pid": pid,
        }

    registered_plugins = [
        plugin_id
        for plugin_id in plugins_snapshot.keys()
        if isinstance(plugin_id, str)
    ]
    running_plugins = [
        plugin_id
        for plugin_id, status in running_plugins_status.items()
        if bool(status.get("alive"))
    ]

    return {
        "plugins_count": len(registered_plugins),
        "registered_plugins": registered_plugins,
        "running_plugins_count": len(running_plugins),
        "running_plugins": running_plugins,
        "running_plugins_status": running_plugins_status,
    }


def _jsonify_setting_value(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        converted: dict[str, object] = {}
        for key_obj, nested_value in value.items():
            converted[str(key_obj)] = _jsonify_setting_value(nested_value)
        return converted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonify_setting_value(item) for item in value]
    return str(value)


def _build_system_config_sync() -> dict[str, object]:
    import plugin.settings as settings

    public_keys_obj = getattr(settings, "PUBLIC_SYSTEM_CONFIG_KEYS", ())
    if isinstance(public_keys_obj, (list, tuple, set, frozenset)):
        keys = [key for key in public_keys_obj if isinstance(key, str)]
    else:
        keys = []

    config: dict[str, object] = {}
    for key in keys:
        try:
            value = getattr(settings, key)
        except AttributeError:
            continue
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.warning(
                "skip system config key '{}': err_type={}",
                key,
                type(exc).__name__,
            )
            continue
        config[key] = _jsonify_setting_value(value)

    return {"config": config}


class AdminQueryService:
    async def get_available(self) -> dict[str, object]:
        try:
            plugins_count = await asyncio.to_thread(_available_snapshot_sync)
            return {
                "status": "ok",
                "available": True,
                "plugins_count": plugins_count,
                "time": now_iso(),
            }
        except _RUNTIME_ERRORS as exc:
            logger.error(
                "get_available failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="AVAILABLE_QUERY_FAILED",
                message="Failed to query server availability",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

    async def get_server_info(self) -> dict[str, object]:
        try:
            info = await asyncio.to_thread(_build_server_info_sync)
            if not isinstance(info, Mapping):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="server info result is not an object",
                    status_code=500,
                    details={"result_type": type(info).__name__},
                )
            normalized_info = dict(info)
            normalized_info["sdk_version"] = SDK_VERSION
            normalized_info["time"] = now_iso()
            return normalized_info
        except ServerDomainError:
            raise
        except _RUNTIME_ERRORS as exc:
            logger.error(
                "get_server_info failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="SERVER_INFO_QUERY_FAILED",
                message="Failed to query server info",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

    async def get_system_config(self) -> dict[str, object]:
        try:
            payload = await asyncio.to_thread(_build_system_config_sync)
            if not isinstance(payload, Mapping):
                raise ServerDomainError(
                    code="INVALID_DATA_SHAPE",
                    message="system config result is not an object",
                    status_code=500,
                    details={"result_type": type(payload).__name__},
                )
            normalized_payload: dict[str, object] = {}
            for key_obj, value in payload.items():
                if not isinstance(key_obj, str):
                    raise ServerDomainError(
                        code="INVALID_DATA_SHAPE",
                        message="system config result contains non-string key",
                        status_code=500,
                        details={"key_type": type(key_obj).__name__},
                    )
                normalized_payload[key_obj] = value
            return normalized_payload
        except ServerDomainError:
            raise
        except (_RUNTIME_ERRORS + (ImportError,)) as exc:
            logger.error(
                "get_system_config failed: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="SYSTEM_CONFIG_QUERY_FAILED",
                message="Failed to query system config",
                status_code=500,
                details={"error_type": type(exc).__name__},
            ) from exc

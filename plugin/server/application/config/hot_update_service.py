from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol, cast

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.infrastructure.config_queries import load_plugin_config
from plugin.server.infrastructure.config_updates import update_plugin_config

logger = get_logger("server.application.config.hot_update")

_KNOWN_RUNTIME_ERRORS = (
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
)


class _SupportsConfigUpdate(Protocol):
    async def send_config_update(
        self,
        config: dict[str, object],
        mode: str = "temporary",
        profile: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, object]:
        ...


def _ensure_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise HTTPException(status_code=400, detail=f"{field} must be an object")
    normalized: dict[str, object] = {}
    for key_obj, item in value.items():
        if not isinstance(key_obj, str):
            raise HTTPException(status_code=400, detail=f"{field} keys must be strings")
        normalized[key_obj] = item
    return normalized


def _get_host_sync(plugin_id: str) -> object | None:
    from plugin.core.state import state

    with state.acquire_plugin_hosts_read_lock():
        return state.plugin_hosts.get(plugin_id)


async def hot_update_plugin_config(
    *,
    plugin_id: str,
    updates: dict[str, object],
    mode: str = "temporary",
    profile: str | None = None,
    timeout: float = 10.0,
) -> dict[str, object]:
    normalized_updates = _ensure_mapping(updates, field="updates")
    loop = asyncio.get_running_loop()
    host = await loop.run_in_executor(None, _get_host_sync, plugin_id)

    if host is None:
        if mode == "temporary":
            raise HTTPException(
                status_code=400,
                detail=f"Plugin {plugin_id} is not running. Cannot apply temporary config update.",
            )
        persisted = await loop.run_in_executor(None, update_plugin_config, plugin_id, normalized_updates)
        persisted["hot_reloaded"] = False
        persisted["mode"] = mode
        return persisted

    if mode == "permanent":
        await loop.run_in_executor(None, update_plugin_config, plugin_id, normalized_updates)

    if not hasattr(host, "send_config_update"):
        raise HTTPException(
            status_code=500,
            detail=f"Plugin {plugin_id} host does not support config hot update",
        )

    if mode == "permanent":
        config_payload = await loop.run_in_executor(None, load_plugin_config, plugin_id)
        full_config_obj = config_payload.get("config")
        if not isinstance(full_config_obj, Mapping):
            raise HTTPException(
                status_code=500,
                detail=f"Plugin {plugin_id} config payload has invalid shape",
            )
        full_config = _ensure_mapping(full_config_obj, field="config")
        update_mode = "temporary"
    else:
        full_config = normalized_updates
        update_mode = mode

    host_adapter = cast(_SupportsConfigUpdate, host)
    try:
        result_obj = await host_adapter.send_config_update(
            config=full_config,
            mode=update_mode,
            profile=profile,
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("Timeout waiting for CONFIG_UPDATE response from plugin {}", plugin_id)
        return {
            "success": True,
            "plugin_id": plugin_id,
            "mode": mode,
            "hot_reloaded": True,
            "requires_reload": False,
            "message": "Config update sent (response timeout, may have been applied)",
        }
    except _KNOWN_RUNTIME_ERRORS as exc:
        logger.warning(
            "CONFIG_UPDATE command failed for plugin {}: err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Plugin config update failed: {str(exc)}",
        ) from exc

    handler_called = False
    if isinstance(result_obj, Mapping):
        handler_called_obj = result_obj.get("handler_called")
        if isinstance(handler_called_obj, bool):
            handler_called = handler_called_obj

    return {
        "success": True,
        "plugin_id": plugin_id,
        "mode": mode,
        "hot_reloaded": True,
        "requires_reload": False,
        "handler_called": handler_called,
        "message": "Config hot-updated successfully",
    }

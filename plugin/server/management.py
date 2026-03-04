"""Legacy management entrypoints.

This module is intentionally thin. All plugin lifecycle logic lives in
`plugin.server.application.plugins.PluginLifecycleService`.
"""
from __future__ import annotations

from fastapi import HTTPException

from plugin.logging_config import get_logger
from plugin.server.application.plugins import PluginLifecycleService
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.management")
_service = PluginLifecycleService()


def _raise_http_from_domain(error: ServerDomainError) -> None:
    logger.warning(
        "management domain error: code={}, status_code={}, message={}",
        error.code,
        error.status_code,
        error.message,
    )
    raise HTTPException(status_code=error.status_code, detail=error.message)


async def start_plugin(plugin_id: str, restore_state: bool = False) -> dict[str, object]:
    try:
        return await _service.start_plugin(plugin_id, restore_state=restore_state)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def stop_plugin(plugin_id: str) -> dict[str, object]:
    try:
        return await _service.stop_plugin(plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def reload_plugin(plugin_id: str) -> dict[str, object]:
    try:
        return await _service.reload_plugin(plugin_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def reload_all_plugins() -> dict[str, object]:
    try:
        return await _service.reload_all_plugins()
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def disable_extension(ext_id: str) -> dict[str, object]:
    try:
        return await _service.disable_extension(ext_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def enable_extension(ext_id: str) -> dict[str, object]:
    try:
        return await _service.enable_extension(ext_id)
    except ServerDomainError as error:
        _raise_http_from_domain(error)


async def freeze_plugin(plugin_id: str) -> dict[str, object]:
    logger.error("freeze_plugin is removed from management API: plugin_id={}", plugin_id)
    raise HTTPException(
        status_code=410,
        detail="freeze_plugin is removed. Use runtime state persistence APIs directly.",
    )


async def unfreeze_plugin(plugin_id: str) -> dict[str, object]:
    logger.error("unfreeze_plugin is removed from management API: plugin_id={}", plugin_id)
    raise HTTPException(
        status_code=410,
        detail="unfreeze_plugin is removed. Use runtime state persistence APIs directly.",
    )

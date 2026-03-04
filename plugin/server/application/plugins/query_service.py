from __future__ import annotations

import asyncio
from collections.abc import Mapping

from plugin.core.status import status_manager
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.utils import now_iso
from plugin.server.services import build_plugin_list

logger = get_logger("server.application.plugins.query")


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
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
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
            raw_plugins = await asyncio.to_thread(build_plugin_list)
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
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
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


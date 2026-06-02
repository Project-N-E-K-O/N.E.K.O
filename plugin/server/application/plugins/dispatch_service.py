from __future__ import annotations

import asyncio
import copy
import math
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.plugins.event_contracts import (
    arbitrate_custom_event_result,
)
from plugin.server.domain import RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.plugins.dispatch")


@runtime_checkable
class HostHealthContract(Protocol):
    alive: bool


@runtime_checkable
class PluginDispatchHostContract(Protocol):
    def health_check(self) -> HostHealthContract: ...

    async def trigger_custom_event(
        self,
        *,
        event_type: str,
        event_id: str,
        args: dict[str, object],
        timeout: float,
    ) -> object: ...


def _resolve_host(plugin_id: str) -> PluginDispatchHostContract:
    hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
    host_obj = hosts_snapshot.get(plugin_id)
    if not isinstance(host_obj, PluginDispatchHostContract):
        raise ServerDomainError(
            code="PLUGIN_NOT_FOUND",
            message=f"Plugin '{plugin_id}' not found",
            status_code=404,
            details={"plugin_id": plugin_id},
        )
    return host_obj


def _normalize_args(raw_args: object) -> dict[str, object]:
    if raw_args is None:
        return {}
    if not isinstance(raw_args, Mapping):
        raise ServerDomainError(
            code="INVALID_ARGUMENT",
            message="args must be an object",
            status_code=400,
            details={},
        )
    normalized: dict[str, object] = {}
    for key, value in raw_args.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="args keys must be strings",
                status_code=400,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _parse_event_handler_key(key: str) -> tuple[str, str, str] | None:
    if ":" in key:
        parts = key.split(":", 2)
        if len(parts) == 3 and all(parts):
            return parts[0], parts[1], parts[2]
        return None
    if "." in key:
        parts = key.split(".", 1)
        if len(parts) == 2 and all(parts):
            return parts[0], "plugin_entry", parts[1]
    return None


def _find_custom_event_handlers(
    event_type: str,
    event_id: str = "",
) -> list[tuple[str, str]]:
    handlers_snapshot = state.get_event_handlers_snapshot_cached(timeout=1.0)
    target_event_id = str(event_id or "").strip()
    matches: set[tuple[str, str]] = set()
    for key_obj, handler_obj in handlers_snapshot.items():
        if not isinstance(key_obj, str):
            continue
        parsed = _parse_event_handler_key(key_obj)
        if parsed is None:
            continue
        plugin_id, key_event_type, key_event_id = parsed
        meta = getattr(handler_obj, "meta", None)
        meta_event_type = getattr(meta, "event_type", None)
        meta_event_id = getattr(meta, "id", None)
        candidate_event_type = (
            meta_event_type
            if isinstance(meta_event_type, str) and meta_event_type
            else key_event_type
        )
        candidate_event_id = (
            meta_event_id
            if isinstance(meta_event_id, str) and meta_event_id
            else key_event_id
        )
        if candidate_event_type != event_type:
            continue
        if target_event_id and candidate_event_id != target_event_id:
            continue
        matches.add((plugin_id, candidate_event_id))
    return sorted(matches)


class PluginDispatchService:
    async def trigger_custom_event(
        self,
        *,
        to_plugin: str,
        event_type: str,
        event_id: str,
        args: object,
        timeout: float,
    ) -> object:
        if not to_plugin:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="to_plugin is required",
                status_code=400,
                details={},
            )
        if not event_type:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="event_type is required",
                status_code=400,
                details={},
            )
        if not event_id:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="event_id is required",
                status_code=400,
                details={},
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="timeout must be a positive finite number",
                status_code=400,
                details={},
            )

        try:
            host = await asyncio.to_thread(_resolve_host, to_plugin)
            health = await asyncio.to_thread(host.health_check)
            if not bool(health.alive):
                raise ServerDomainError(
                    code="PLUGIN_NOT_READY",
                    message=f"Plugin '{to_plugin}' process is not alive",
                    status_code=409,
                    details={"plugin_id": to_plugin},
                )
            normalized_args = _normalize_args(args)
            return await host.trigger_custom_event(
                event_type=event_type,
                event_id=event_id,
                args=normalized_args,
                timeout=timeout,
            )
        except ServerDomainError:
            raise
        except RUNTIME_ERRORS as exc:
            logger.error(
                "trigger_custom_event failed: to_plugin={}, event_type={}, event_id={}, err_type={}, err={}",
                to_plugin,
                event_type,
                event_id,
                type(exc).__name__,
                str(exc),
            )
            raise ServerDomainError(
                code="PLUGIN_EVENT_DISPATCH_FAILED",
                message="Failed to dispatch plugin event",
                status_code=500,
                details={"error_type": type(exc).__name__, "to_plugin": to_plugin},
            ) from exc

    async def trigger_custom_event_subscribers(
        self,
        *,
        event_type: str,
        event_id: str = "",
        args: object,
        timeout: float,
    ) -> list[dict[str, object]]:
        if not event_type:
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="event_type is required",
                status_code=400,
                details={},
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or float(timeout) <= 0
        ):
            raise ServerDomainError(
                code="INVALID_ARGUMENT",
                message="timeout must be a positive finite number",
                status_code=400,
                details={},
            )

        target_event_id = str(event_id or "").strip()
        normalized_args = _normalize_args(args)
        handlers = await asyncio.to_thread(
            _find_custom_event_handlers,
            event_type,
            target_event_id,
        )

        async def _dispatch_handler(plugin_id: str, handler_event_id: str) -> dict[str, object]:
            try:
                handler_args = copy.deepcopy(normalized_args)
                result = await self.trigger_custom_event(
                    to_plugin=plugin_id,
                    event_type=event_type,
                    event_id=handler_event_id,
                    args=handler_args,
                    timeout=timeout,
                )
                return {
                    "plugin_id": plugin_id,
                    "event_id": handler_event_id,
                    "success": True,
                    "result": result,
                }
            except ServerDomainError as exc:
                logger.warning(
                    "trigger_custom_event_subscribers handler failed: plugin_id={}, event_type={}, event_id={}, code={}, message={}",
                    plugin_id,
                    event_type,
                    handler_event_id,
                    exc.code,
                    exc.message,
                )
                return {
                    "plugin_id": plugin_id,
                    "event_id": handler_event_id,
                    "success": False,
                    "code": exc.code,
                    "error": exc.message,
                }
            except Exception as exc:
                logger.warning(
                    "trigger_custom_event_subscribers handler crashed: plugin_id={}, event_type={}, event_id={}, err_type={}, err={}",
                    plugin_id,
                    event_type,
                    handler_event_id,
                    type(exc).__name__,
                    str(exc),
                )
                return {
                    "plugin_id": plugin_id,
                    "event_id": handler_event_id,
                    "success": False,
                    "code": "PLUGIN_EVENT_DISPATCH_FAILED",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }

        if not handlers:
            return []
        return list(
            await asyncio.gather(
                *(_dispatch_handler(plugin_id, event_id) for plugin_id, event_id in handlers)
            )
        )

    async def trigger_arbitrated_custom_event(
        self,
        *,
        event_type: str,
        event_id: str = "",
        args: object,
        timeout: float,
    ) -> dict[str, object]:
        dispatch_results = await self.trigger_custom_event_subscribers(
            event_type=event_type,
            event_id=event_id,
            args=args,
            timeout=timeout,
        )
        return arbitrate_custom_event_result(
            event_type=event_type,
            dispatch_results=dispatch_results,
        )

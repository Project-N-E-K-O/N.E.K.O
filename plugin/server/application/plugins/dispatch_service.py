from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from plugin.core.state import state
from plugin.logging_config import get_logger
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
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError, TimeoutError) as exc:
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

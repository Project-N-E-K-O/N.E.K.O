from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from plugin._types.errors import ErrorCode
from plugin._types.exceptions import PluginError
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.utils.time_utils import now_iso
from plugin.core.responses import fail, is_envelope, ok
from plugin.sdk.shared.core.entry_runtime import resolve_entry_timeout
from plugin.settings import PLUGIN_EXECUTION_TIMEOUT

logger = get_logger("server.runs.trigger")
_TIMEOUT_UNSET = object()


class TriggerResult(BaseModel):
    success: bool
    plugin_id: str
    entry_id: str
    args: dict[str, object] = Field(default_factory=dict)
    plugin_response: object = None
    received_at: str = ""


def _normalize_timeout_value(value: object) -> float | None | object:
    if value is _TIMEOUT_UNSET:
        return _TIMEOUT_UNSET
    if value is None:
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return _TIMEOUT_UNSET
    return timeout if timeout > 0 else None


@runtime_checkable
class HostHealthContract(Protocol):
    alive: bool
    status: object
    pid: object
    exitcode: object


@runtime_checkable
class TriggerHostContract(Protocol):
    async def trigger(self, entry_id: str, args: dict[str, object], timeout: float | None) -> object: ...

    def health_check(self) -> HostHealthContract: ...


def _enqueue_trigger_event(event: Mapping[str, object]) -> None:
    normalized = dict(event)
    trace_id_obj = normalized.get("trace_id")
    trace_id = trace_id_obj if isinstance(trace_id_obj, str) and trace_id_obj else str(uuid.uuid4())
    normalized["trace_id"] = trace_id

    event_id_obj = normalized.get("event_id")
    if not isinstance(event_id_obj, str) or not event_id_obj:
        normalized["event_id"] = trace_id

    received_at_obj = normalized.get("received_at")
    if not isinstance(received_at_obj, str) or not received_at_obj:
        normalized["received_at"] = now_iso()

    event_queue = state.event_queue
    try:
        event_queue.put_nowait(normalized)
    except asyncio.QueueFull:
        try:
            event_queue.get_nowait()
            event_queue.put_nowait(normalized)
        except (asyncio.QueueEmpty, RuntimeError, AttributeError):
            logger.debug("event queue overflow during trigger event enqueue")
    except (RuntimeError, AttributeError):
        logger.debug("event queue unavailable during trigger event enqueue")

    try:
        state.append_event_record(normalized)
    except (RuntimeError, ValueError, TypeError, AttributeError):
        logger.debug("failed to append trigger event record")


def _resolve_host(plugin_id: str, trace_id: str) -> tuple[TriggerHostContract | None, dict[str, object] | None]:
    try:
        plugins_snapshot = state.get_plugins_snapshot_cached(timeout=1.0)
        hosts_snapshot = state.get_plugin_hosts_snapshot_cached(timeout=1.0)
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning(
            "failed to get host snapshots for plugin {}: err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )
        return None, fail(
            ErrorCode.NOT_READY,
            "System is busy, please retry",
            details={"hint": "State snapshots unavailable"},
            retriable=True,
            trace_id=trace_id,
        )
    if not isinstance(plugins_snapshot, Mapping) or not isinstance(hosts_snapshot, Mapping):
        logger.warning(
            "invalid snapshot shape for plugin {}: plugins_type={}, hosts_type={}",
            plugin_id,
            type(plugins_snapshot).__name__,
            type(hosts_snapshot).__name__,
        )
        return None, fail(
            ErrorCode.NOT_READY,
            "System is busy, please retry",
            details={"hint": "State snapshots invalid"},
            retriable=True,
            trace_id=trace_id,
        )

    host_obj = hosts_snapshot.get(plugin_id)
    if not isinstance(host_obj, TriggerHostContract):
        plugin_registered = plugin_id in plugins_snapshot
        running_plugins = [
            key
            for key in hosts_snapshot.keys()
            if isinstance(key, str)
        ]
        if plugin_registered:
            return None, fail(
                ErrorCode.NOT_READY,
                f"Plugin '{plugin_id}' is registered but not running",
                details={
                    "hint": f"Start the plugin via POST /plugin/{plugin_id}/start",
                    "running_plugins": running_plugins,
                },
                retriable=True,
                trace_id=trace_id,
            )
        known_plugins = [
            key
            for key in plugins_snapshot.keys()
            if isinstance(key, str)
        ]
        return None, fail(
            ErrorCode.NOT_FOUND,
            f"Plugin '{plugin_id}' is not found/registered",
            details={"known_plugins": known_plugins},
            trace_id=trace_id,
        )

    try:
        health = host_obj.health_check()
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning(
            "health check failed for plugin {}: err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )
        return None, fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health check failed",
            details={"error": str(exc)},
            retriable=True,
            trace_id=trace_id,
        )

    alive_obj = getattr(health, "alive", None)
    status_obj = getattr(health, "status", None)
    pid_obj = getattr(health, "pid", None)
    exitcode_obj = getattr(health, "exitcode", None)

    if alive_obj is None:
        return None, fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health payload is invalid",
            details={"hint": "health_check contract mismatch"},
            retriable=True,
            trace_id=trace_id,
        )

    try:
        alive = bool(alive_obj)
    except (RuntimeError, ValueError, TypeError):
        return None, fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' health payload is invalid",
            details={"hint": "health_check contract mismatch"},
            retriable=True,
            trace_id=trace_id,
        )

    if not alive:
        return None, fail(
            ErrorCode.NOT_READY,
            f"Plugin '{plugin_id}' process is not alive",
            details={
                "status": status_obj,
                "pid": pid_obj,
                "exitcode": exitcode_obj,
            },
            retriable=True,
            trace_id=trace_id,
        )

    return host_obj, None


async def _execute_trigger(
    *,
    host: TriggerHostContract,
    plugin_id: str,
    entry_id: str,
    args: dict[str, object],
    trace_id: str,
) -> object:
    resolved_timeout: float | None = PLUGIN_EXECUTION_TIMEOUT
    try:
        handlers_snapshot = state.get_event_handlers_snapshot_cached(timeout=1.0)
        prefix_dot = f"{plugin_id}."
        prefix_colon = f"{plugin_id}:plugin_entry:"
        for event_key_obj, handler_obj in handlers_snapshot.items():
            if not isinstance(event_key_obj, str):
                continue
            if not (event_key_obj.startswith(prefix_dot) or event_key_obj.startswith(prefix_colon)):
                continue
            meta = getattr(handler_obj, "meta", None)
            if getattr(meta, "event_type", None) != "plugin_entry":
                continue
            if getattr(meta, "id", None) != entry_id:
                continue
            resolved_timeout = resolve_entry_timeout(meta, resolved_timeout)
            break
    except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.debug(
            "failed to resolve per-entry timeout for plugin {} entry {}: err_type={}, err={}",
            plugin_id,
            entry_id,
            type(exc).__name__,
            str(exc),
        )

    try:
        ctx_obj = args.get("_ctx")
        if isinstance(ctx_obj, Mapping):
            requested_timeout = _normalize_timeout_value(
                ctx_obj.get("entry_timeout", _TIMEOUT_UNSET),
            )
            if requested_timeout is not _TIMEOUT_UNSET:
                resolved_timeout = requested_timeout
    except Exception:
        logger.debug("failed to read requested entry timeout from _ctx", exc_info=True)

    try:
        response = await host.trigger(entry_id, args, timeout=resolved_timeout)
        logger.debug("plugin trigger response received: plugin_id={}, entry_id={}", plugin_id, entry_id)
        return response
    except (TimeoutError, asyncio.TimeoutError):
        return fail(
            ErrorCode.TIMEOUT,
            "Plugin execution timed out",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "timeout": resolved_timeout},
            retriable=True,
            trace_id=trace_id,
        )
    except PluginError as exc:
        return fail(
            ErrorCode.INTERNAL,
            str(exc),
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(exc).__name__},
            trace_id=trace_id,
        )
    except (ConnectionError, OSError) as exc:
        return fail(
            ErrorCode.NOT_READY,
            "Communication error with plugin",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(exc).__name__},
            retriable=True,
            trace_id=trace_id,
        )
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "Invalid request parameters",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(exc).__name__},
            trace_id=trace_id,
        )
    except RuntimeError as exc:
        return fail(
            ErrorCode.INTERNAL,
            "An internal error occurred",
            details={"plugin_id": plugin_id, "entry_id": entry_id, "type": type(exc).__name__},
            trace_id=trace_id,
        )


def _normalize_plugin_response(plugin_response: object, trace_id: str) -> dict[str, object]:
    if not is_envelope(plugin_response):
        if isinstance(plugin_response, Mapping):
            return ok(data=dict(plugin_response), trace_id=trace_id)
        if plugin_response is None:
            return ok(trace_id=trace_id)
        return ok(data=plugin_response, trace_id=trace_id)

    if not isinstance(plugin_response, Mapping):
        return ok(trace_id=trace_id)

    normalized_response = dict(plugin_response)
    trace_id_obj = normalized_response.get("trace_id")
    if not isinstance(trace_id_obj, str) or not trace_id_obj:
        normalized_response["trace_id"] = trace_id
    return normalized_response


async def trigger_plugin(
    *,
    plugin_id: str,
    entry_id: str,
    args: dict[str, object],
    task_id: str | None = None,
    client_host: str | None = None,
) -> TriggerResult:
    trace_id = str(uuid.uuid4())
    received_at = now_iso()

    _enqueue_trigger_event(
        {
            "type": "plugin_triggered",
            "plugin_id": plugin_id,
            "entry_id": entry_id,
            "args": dict(args),
            "task_id": task_id,
            "client": client_host,
            "received_at": received_at,
            "trace_id": trace_id,
        }
    )

    host, resolve_error = _resolve_host(plugin_id, trace_id)
    if resolve_error is not None:
        return TriggerResult(
            success=False,
            plugin_id=plugin_id,
            entry_id=entry_id,
            args=dict(args),
            plugin_response=resolve_error,
            received_at=received_at,
        )

    response = await _execute_trigger(
        host=host,
        plugin_id=plugin_id,
        entry_id=entry_id,
        args=args,
        trace_id=trace_id,
    )
    normalized_response = _normalize_plugin_response(response, trace_id)
    return TriggerResult(
        success=bool(normalized_response.get("success")),
        plugin_id=plugin_id,
        entry_id=entry_id,
        args=dict(args),
        plugin_response=normalized_response,
        received_at=received_at,
    )

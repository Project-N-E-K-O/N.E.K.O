"""The invocation contract shared by ordinary and cross-plugin entry triggers."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from typing import Any

from plugin._types.exceptions import PluginEntryNotFoundError, PluginExecutionError
from plugin.core.rpc import (
    RpcDeadlineExceededError,
    log_rpc_failure,
    remaining_budget,
    request_deadline,
    request_scope,
)
from plugin.sdk.shared.core.entry_runtime import (
    prepare_entry_kwargs,
    resolve_entry_timeout,
)

UNSET_TIMEOUT = object()


class EntryExecutor:
    """Own entry preparation and execution, without knowing about sockets or replies."""

    def __init__(
        self,
        *,
        plugin_id: str,
        context: Any,
        instance: Any,
        entries: Mapping[str, Any],
        metadata: Mapping[str, Any],
        events: Mapping[str, Mapping[str, Any]],
        default_timeout: float,
        refresh: Callable[[], None],
        should_persist: Callable[[Any], bool],
        persist: Callable[[str], Awaitable[None]],
        logger: Any,
    ):
        self.plugin_id = plugin_id
        self._context, self._instance = context, instance
        self._entries, self._metadata, self._events = entries, metadata, events
        self._default_timeout = default_timeout
        self._refresh, self._should_persist, self._persist = (
            refresh,
            should_persist,
            persist,
        )
        self._logger = logger

    def _resolve(self, event_type: str, event_id: str, *, registered_only: bool):
        # Custom-event calls (including cross-plugin ones) reach only handlers
        # registered for that event type; the attribute fallback would expose
        # any public or private instance method by name.
        if registered_only or event_type != "plugin_entry":
            return self._events.get(event_type, {}).get(event_id)
        method = (
            self._entries.get(event_id)
            or getattr(self._instance, event_id, None)
            or getattr(self._instance, f"entry_{event_id}", None)
        )
        if method is None:
            self._refresh()
            method = self._entries.get(event_id)
        return method

    def _timeout(self, event_id: str, requested: Any) -> float | None:
        # Keep existing direct-entry configuration semantics. The RPC deadline
        # remains a separate hard upper limit even for an unlimited entry.
        configured = resolve_entry_timeout(self._metadata.get(event_id), UNSET_TIMEOUT)
        has_configured = configured is not UNSET_TIMEOUT
        if requested is not UNSET_TIMEOUT:
            if requested is None:
                return configured if has_configured else None
            explicit = float(requested)
            if explicit <= 0:
                return configured if has_configured else None
            if has_configured:
                return None if configured is None else min(explicit, configured)
            return explicit
        return configured if has_configured else self._default_timeout

    async def invoke(
        self,
        event_type: str,
        event_id: str,
        args: dict[str, Any],
        *,
        timeout: Any = UNSET_TIMEOUT,
        deadline: float | None = None,
        registered_only: bool = False,
    ) -> object:
        if not isinstance(args, dict) or not event_id:
            raise PluginExecutionError(
                self.plugin_id, event_id, "Entry ID and object arguments are required"
            )
        method = self._resolve(event_type, event_id, registered_only=registered_only)
        if method is None:
            raise PluginEntryNotFoundError(self.plugin_id, event_id)
        if not inspect.iscoroutinefunction(method):
            raise PluginExecutionError(
                self.plugin_id, event_id, "Entry must be 'async def'"
            )
        label = f"{event_type}.{event_id}"
        budget = self._timeout(event_id, timeout)
        budget = remaining_budget(budget, deadline)
        if budget is not None and budget <= 0:
            raise RpcDeadlineExceededError(f"{label} deadline expired before execution")
        call_args = (
            prepare_entry_kwargs(
                plugin_id=self.plugin_id,
                entry_id=event_id,
                handler=method,
                meta=self._metadata.get(event_id),
                args=args,
            )
            if event_type == "plugin_entry"
            else dict(args)
        )
        host_context = args.get("_ctx")
        host_context = host_context if isinstance(host_context, dict) else {}
        with ExitStack() as scopes:
            scopes.enter_context(self._context._handler_scope(label))
            scopes.enter_context(self._context._run_scope(host_context.get("run_id")))
            scopes.enter_context(
                self._context._lanlan_scope(host_context.get("lanlan_name"))
            )
            task = asyncio.create_task(method(**call_args))

            async def watchdog():
                while not task.done():
                    start = time.monotonic()
                    await asyncio.sleep(1)
                    elapsed = time.monotonic() - start
                    if elapsed > 5 and not task.done():
                        self._logger.warning(
                            f"Plugin event-loop stall: entry={label}, elapsed={elapsed:.1f}s"
                        )

            monitor = asyncio.create_task(watchdog())
            timer = asyncio.timeout(budget)
            try:
                async with timer:
                    result = await task
            except TimeoutError:
                if timer.expired():
                    raise RpcDeadlineExceededError(
                        f"{label} execution timed out after {budget:g}s"
                    ) from None
                raise
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            if event_type == "plugin_entry" and self._should_persist(method):
                try:
                    await self._persist("auto")
                except Exception as exc:
                    log_rpc_failure(
                        self._logger,
                        f"Plugin state persistence failed: entry={label}",
                        exc,
                    )
            if hasattr(result, "is_ok") and callable(result.is_ok):
                if not result.is_ok():
                    raise PluginExecutionError(
                        self.plugin_id, event_id, str(result.error)
                    )
                result = result.value
            return result


async def execute_entry_command(
    executor: EntryExecutor, message: dict[str, Any]
) -> dict[str, Any]:
    """Legacy command codec; both wire trigger names use the same executor."""
    event_type = str(message.get("event_type") or "plugin_entry")
    event_id = str(message.get("event_id") or message.get("entry_id") or "")
    reply = {
        "req_id": message.get("req_id"),
        "success": False,
        "data": None,
        "error": None,
    }
    try:
        deadline = request_deadline(message, None)
        with request_scope(message):
            reply["data"] = await executor.invoke(
                event_type,
                event_id,
                message.get("args", {}),
                timeout=message.get("timeout", UNSET_TIMEOUT),
                deadline=deadline,
                registered_only=message.get("type") == "TRIGGER_CUSTOM",
            )
        reply["success"] = True
    except asyncio.CancelledError:
        reply["error"] = {"code": "CANCELLED", "message": "Execution cancelled"}
    except RpcDeadlineExceededError as exc:
        reply["error"] = {
            "code": "DEADLINE_EXCEEDED",
            "message": str(exc) or "Entry execution timed out",
        }
        executor._logger.warning(
            f"Plugin entry timed out: entry={event_type}.{event_id}, req_id={reply['req_id']}"
        )
    except TimeoutError as exc:
        reply["error"] = {
            "code": "OPERATION_TIMEOUT",
            "message": str(exc) or "Entry operation timed out",
        }
        executor._logger.warning(
            f"Plugin entry operation timed out: entry={event_type}.{event_id}, req_id={reply['req_id']}"
        )
    except Exception as exc:
        reply["error"] = {
            "code": "PLUGIN_EXECUTION_FAILED",
            "message": str(exc),
            "details": {
                "plugin_id": executor.plugin_id,
                "event_id": event_id,
                "error_type": type(exc).__name__,
            },
        }
        executor._logger.warning(
            f"Plugin entry failed: entry={event_type}.{event_id}, req_id={reply['req_id']}, error_type={type(exc).__name__}"
        )
    return reply

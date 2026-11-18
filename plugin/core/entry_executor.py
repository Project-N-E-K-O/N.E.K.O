"""The invocation contract shared by ordinary and cross-plugin entry triggers."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import ExitStack
from typing import Any

from plugin._types.exceptions import (
    OUTPUT_SCHEMA_VIOLATION_CODE,
    PluginEntryNotFoundError,
    PluginExecutionError,
    PluginOutputSchemaError,
)
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
from plugin.core.responses import is_envelope
from plugin.sdk.shared.core.output_contract import (
    OutputCheck,
    build_output_check,
    format_issues,
    is_violation,
)

UNSET_TIMEOUT = object()
# Output-contract warnings: the same mismatch of the same entry is logged at
# most once per interval; the next warning reports how many were suppressed.
_WARN_INTERVAL = 60.0
_WARN_KEYS_MAX = 1024
# Keys ok(), fail() and finish() all write (``meta`` is optional).
_ENVELOPE_KEYS = frozenset({"success", "code", "data", "message", "error", "time", "trace_id"})


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
        # entry id -> (the meta the check was built from, the check). Rebuilt
        # only when the entry's meta object changes (dynamic re-registration).
        self._output_checks: dict[str, tuple[object, OutputCheck | None]] = {}
        # (entry id, warning key) -> (last warning time, warnings suppressed since).
        self._warned: dict[tuple[str, str], tuple[float, int]] = {}

    def _output_check(self, event_id: str, meta: object) -> OutputCheck | None:
        if len(self._output_checks) > len(self._metadata):
            # More cached checks than entries: some entries are gone. Drop
            # them even when this call is a cache hit.
            self._prune_output_checks()
        cached = self._output_checks.get(event_id)
        if cached is not None and cached[0] is meta:
            return cached[1]
        # Entries that are gone (unregistered dynamic entries) leave the cache
        # here, so it tracks live entries rather than every id ever called.
        self._prune_output_checks()
        try:
            check = build_output_check(meta)
        except Exception as exc:
            # A contract that cannot be compiled must not break the entry.
            check = None
            self._logger.warning(
                f"Plugin entry output_schema could not be prepared: entry={event_id}, error_type={type(exc).__name__}"
            )
        # A call that started before a re-registration still checks against
        # its own meta, but must not overwrite the current entry's cache.
        if self._metadata.get(event_id) is meta:
            self._output_checks[event_id] = (meta, check)
        return check

    def _prune_output_checks(self) -> None:
        for stale in [key for key in self._output_checks if key not in self._metadata]:
            del self._output_checks[stale]
        for gone in [key for key in self._warned if key[0] not in self._metadata]:
            del self._warned[gone]

    def _warn_limited(self, event_id: str, key: str, message: str) -> None:
        """Log ``message`` at most once per ``_WARN_INTERVAL`` for the same entry and ``key``."""
        now = time.monotonic()
        last = self._warned.get((event_id, key))
        if last is not None and now - last[0] < _WARN_INTERVAL:
            self._warned[(event_id, key)] = (last[0], last[1] + 1)
            return
        if len(self._warned) >= _WARN_KEYS_MAX:
            # Drop records whose window is over first; only if that is not
            # enough, start over rather than grow without bound.
            for stale in [k for k, (at, _n) in self._warned.items() if now - at >= _WARN_INTERVAL]:
                del self._warned[stale]
            if len(self._warned) >= _WARN_KEYS_MAX:
                self._warned.clear()
        self._warned[(event_id, key)] = (now, 0)
        suffix = f", suppressed={last[1]}" if last is not None and last[1] else ""
        self._logger.warning(f"{message}{suffix}")

    def _check_output(self, event_id: str, label: str, result: object, meta: object) -> None:
        """Validate a successful result against the entry's output contract.

        ``meta`` is the entry's metadata as resolved when the call started; a
        re-registration while the handler ran does not change the contract.
        Logs and errors carry field paths and error types only, never the data.
        """
        if meta is None:
            return
        check = self._output_check(event_id, meta)
        if check is None:
            return
        try:
            if hasattr(result, "is_ok") and callable(result.is_ok):
                # Ok: check its value. Err is a failure already: not checked.
                if not result.is_ok():
                    return
                result = result.value
            if is_envelope(result) and _ENVELOPE_KEYS.issubset(result.keys()):
                # finish()/ok() envelopes: the contract describes ``data``;
                # a fail() envelope is a failure already and is not checked.
                # All keys every producer writes: a model may have a few of them.
                if result.get("success") is not True:
                    return
                result = result.get("data")
            issues = check(result)
        except Exception as exc:
            # The checkers catch their own failures; anything left (a mapping
            # that raises when read, a metaclass raising in isinstance) is a
            # checker failure, never a reason to lose the handler's result.
            self._warn_limited(
                event_id,
                f"unchecked:{type(exc).__name__}",
                f"Plugin entry output could not be checked: entry={label}, error_type={type(exc).__name__}",
            )
            return
        if not issues:
            return
        summary = format_issues(issues)
        strict = bool(getattr(meta, "strict_output", False)) and is_violation(issues)
        self._warn_limited(
            event_id,
            # Indexes vary with the data; the kind of mismatch does not.
            re.sub(r"\[\d+\]", "[]", summary),
            f"Plugin entry output does not match output_schema: entry={label}, strict={strict}, issues={summary}",
        )
        if strict:
            raise PluginOutputSchemaError(
                self.plugin_id,
                event_id,
                [issue.as_dict() for issue in issues],
                summary,
            )

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
        # The contract this call is checked against: resolved with the handler,
        # not re-read after it returns.
        call_meta = self._metadata.get(event_id) if event_type == "plugin_entry" else None
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
            if event_type == "plugin_entry":
                # Before persisting: a strict violation fails the call and the
                # automatic state snapshot is skipped. The handler's own side
                # effects have already happened and are not rolled back.
                self._check_output(event_id, label, result, call_meta)
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
    except PluginOutputSchemaError as exc:
        # Already logged (paths and types only) where it was detected.
        reply["error"] = {
            "code": OUTPUT_SCHEMA_VIOLATION_CODE,
            "message": str(exc),
            "details": {
                "plugin_id": executor.plugin_id,
                "event_id": event_id,
                "error_type": type(exc).__name__,
                "issues": list(exc.issues),
            },
        }
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

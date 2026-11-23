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
    narrow_scope_deadline,
    remaining_budget,
    request_deadline,
    request_scope,
)
from plugin.sdk.shared.core.entry_runtime import (
    prepare_entry_kwargs,
    resolve_entry_timeout,
)
from plugin.core.responses import is_envelope
from plugin.sdk.shared.constants import EVENT_META_ATTR
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


# Default of EntryExecutor(maps_generation=...): the maps reflect the instance's current generation.
_CURRENT = object()

# Deprecation grace for plugin_entry calls to a public method that neither
# collect_entries() registers nor the manifest's ``entries`` table declares.
# While True such a call still runs and logs a rate-limited warning; set to
# False (planned for the next release) to refuse it like any unknown id.
UNDECLARED_ENTRY_GRACE = True
# Module prefix of the SDK: methods its base classes define are never entries.
_SDK_MODULE_PREFIX = "plugin.sdk."


def _defined_by_sdk(instance: Any, name: str) -> bool:
    """Whether ``name`` is an attribute some SDK base class (or ``object``) defines.

    Checked on the whole MRO, so a plugin overriding a base method does not
    turn it into an entry either.
    """
    for klass in type(instance).__mro__:
        if klass is object or str(getattr(klass, "__module__", "")).startswith(_SDK_MODULE_PREFIX):
            if name in vars(klass):
                return True
    return False


def _event_type_of(meta: Any) -> str:
    return str(getattr(meta, "event_type", None) or "plugin_entry")


def _member_event_meta(member: Any) -> Any:
    meta = getattr(member, EVENT_META_ATTR, None)
    if meta is None:
        wrapped = getattr(member, "__wrapped__", None)
        if wrapped is not None:
            meta = getattr(wrapped, EVENT_META_ATTR, None)
    return meta


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
        maps_generation: Any = _CURRENT,
        manifest_entry_ids: Any = None,
    ):
        self.plugin_id = plugin_id
        self._context, self._instance = context, instance
        self._entries, self._metadata, self._events = entries, metadata, events
        # Components generation the maps reflect (see _resolve). The host
        # passes the one read when it built the maps: changes made between
        # then and now (in on_startup) still trigger a rebuild. Left out, the
        # maps are taken to be current.
        seen = getattr(instance, "_components_generation", None) if maps_generation is _CURRENT else maps_generation
        self._seen_generation = seen if isinstance(seen, int) else None
        self._default_timeout = default_timeout
        self._refresh, self._should_persist, self._persist = (
            refresh,
            should_persist,
            persist,
        )
        self._logger = logger
        # Entry ids the effective manifest's ``entries`` table declares, sent
        # by the host at launch. Not sent (``None``): nothing counts as
        # declared there, so plain methods fall under the deprecation grace.
        self._manifest_entry_ids: frozenset[str] = (
            frozenset(str(eid) for eid in manifest_entry_ids) if manifest_entry_ids is not None else frozenset()
        )
        # entry id -> (the meta the check was built from, the check). Rebuilt
        # only when the entry's meta object changes (dynamic re-registration).
        self._output_checks: dict[str, tuple[object, OutputCheck | None]] = {}
        # (entry id, warning key) -> (last warning time, warnings suppressed since).
        self._warned: dict[tuple[str, str], tuple[float, int]] = {}
        # Same records for deprecation warnings of undeclared entries. Kept
        # apart: _prune_output_checks drops ids that are not registered,
        # which these never are.
        self._deprecation_warned: dict[tuple[str, str], tuple[float, int]] = {}

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

    def _warn_limited(
        self,
        event_id: str,
        key: str,
        message: str,
        *,
        records: dict[tuple[str, str], tuple[float, int]] | None = None,
    ) -> None:
        """Log ``message`` at most once per ``_WARN_INTERVAL`` for the same entry and ``key``."""
        warned = self._warned if records is None else records
        now = time.monotonic()
        last = warned.get((event_id, key))
        if last is not None and now - last[0] < _WARN_INTERVAL:
            warned[(event_id, key)] = (last[0], last[1] + 1)
            return
        if len(warned) >= _WARN_KEYS_MAX:
            # Drop records whose window is over first; only if that is not
            # enough, start over rather than grow without bound.
            for stale in [k for k, (at, _n) in warned.items() if now - at >= _WARN_INTERVAL]:
                del warned[stale]
            if len(warned) >= _WARN_KEYS_MAX:
                warned.clear()
        warned[(event_id, key)] = (now, 0)
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
        generation = getattr(self._instance, "_components_generation", None)
        if isinstance(generation, int) and generation != self._seen_generation:
            # Components changed since the maps were built (a router included
            # or excluded, a runtime entry added or removed): rebuild them once,
            # for every event type, so a stale handler is not served.
            self._seen_generation = generation
            try:
                self._refresh()
            except Exception:
                # The plugin's own collection is failing: keep serving from
                # the existing maps (as before), but entries go through the
                # state check below instead of the fast path, and the next
                # call tries the rebuild again.
                self._seen_generation = None
        # Custom-event calls (including cross-plugin ones) reach only handlers
        # registered for that event type; the attribute fallback below is for
        # plugin_entry calls only.
        if registered_only or event_type != "plugin_entry":
            return self._events.get(event_type, {}).get(event_id)
        # Permission narrowing: an id the check closed (a refused component,
        # or an entry hook listing an undeclared permission, including for an
        # entry only the manifest declares) is not run, whether it sits in the
        # map or would come from the attribute fallback. An id registered again with a valid
        # narrowing is "registered" and is picked up by a refresh.
        method = self._mapped_entry(event_id)
        if method is not None and event_id in self._metadata and self._seen_generation == generation:
            # Mapped from collect_entries() (it carries metadata), which
            # already ran the check: no per-call cost for registered entries.
            return method
        state_of = getattr(self._instance, "_entry_state", None)
        try:
            state = state_of(event_id) if callable(state_of) else None
        except Exception:
            # The check must not make an entry unreachable by failing itself;
            # collect_entries() raises the same error on the next refresh.
            state = None
        if state == "closed":
            return None
        if method is None and state == "registered":
            self._refresh()
            method = self._mapped_entry(event_id)
        if method is None:
            method = self._undeclared_method(event_id)
        if method is None:
            self._refresh()
            method = self._mapped_entry(event_id)
        return method

    def _mapped_entry(self, event_id: str):
        """The plugin_entry handler the maps hold for ``event_id``, if any.

        A handler of another event type sharing the id is not served as a
        plugin_entry. (The maps hold only collected or decorated handlers:
        the loader does not map members without event metadata.)
        """
        method = self._entries.get(event_id)
        if method is None:
            return None
        meta = self._metadata.get(event_id)
        if meta is None or _event_type_of(meta) == "plugin_entry":
            return method
        # The id map holds one handler per id; the per-type map keeps an
        # entry that shares its id with another event type.
        return self._events.get("plugin_entry", {}).get(event_id)

    def _undeclared_method(self, event_id: str):
        """Resolve an id the maps do not hold to a public plugin method, or ``None``.

        Reached for entries only the manifest declares (plain methods) and,
        during the deprecation grace, for public methods nothing declares.
        Never resolves private names, SDK base-class methods, or handlers of
        another event type.
        """
        if event_id.startswith("_") or _defined_by_sdk(self._instance, event_id):
            return None
        method = getattr(self._instance, event_id, None)
        if not method or not callable(method):
            return None
        meta = _member_event_meta(method)
        if meta is not None and _event_type_of(meta) != "plugin_entry":
            return None
        if event_id in self._manifest_entry_ids:
            return method
        if not UNDECLARED_ENTRY_GRACE:
            return None
        if inspect.iscoroutinefunction(method):
            # The id only, never the arguments; at most once per interval per id.
            self._warn_limited(
                event_id,
                "undeclared",
                "Deprecated plugin_entry call: entry="
                f"{event_id[:64]!r} is neither registered nor declared in plugin.toml entries; "
                "it still runs in this version and will be refused in the next one",
                records=self._deprecation_warned,
            )
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
        if budget is not None:
            # The entry's own budget is also the deadline of everything it
            # calls: nested requests take the earlier of it and their own.
            narrow_scope_deadline(time.monotonic() + budget)
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

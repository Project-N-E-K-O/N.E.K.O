# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Calling a plugin entry: the one piece of code every caller goes through.

The task brain calling an entry directly and a task agent calling it through the
capability gateway both end in ``PluginRunner.run_entry``. It sends the existing
``POST /runs`` request to the plugin server, polls ``GET /runs/{id}``, reads
the result from ``GET /runs/{id}/export`` and, when the commission is stopped,
calls ``POST /runs/{id}/cancel``. Nothing on the plugin side changes.

The request a plugin receives is what the analyzer sent before
(``brain/task_executor.py``): the same body, the same ``args._ctx`` keys
(``lanlan_name``, ``conversation_id``, ``entry_timeout``,
``latest_user_request``), the same entry checks. ``task_id`` carries the
commission id, so the plugin's runs are recorded under the commission.

``plugin_terminal_status``, ``resolve_delivery_mode`` and
``resolve_result_contract`` read a result the way
``app/agent_server/channels/user_plugin.py`` does. The contract tests in
``tests/unit/nekoclaw/test_contract_plugin_*.py`` hold both sides to the same
expectations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nekoclaw.host.selection import visible_entries

logger = logging.getLogger(__name__)

RUN_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "timeout"})
CATALOG_TTL_SECONDS = 30.0
CATALOG_FETCH_TIMEOUT_SECONDS = 1.0
POLL_INTERVAL_SECONDS = 0.5
MAX_CONSECUTIVE_POLL_ERRORS = 3
_CANCEL_MEMORY = 512
_LLM_TOOL_ENTRY_PREFIX = "__llm_tool__"
_TIMEOUT_UNSET = object()

ClientFactory = Callable[..., Any]
ProgressCallback = Callable[..., Awaitable[None]]


def _default_client_factory(**kwargs: Any) -> Any:
    import httpx

    return httpx.AsyncClient(**kwargs)


def _reraise_swallowed_cancellation() -> None:
    """Raise ``CancelledError`` if this task was cancelled and something below hid it.

    A cancellation can surface as an ordinary error from the layer that was
    awaited, or be absorbed there altogether. Carrying on would keep polling a
    run whose caller is gone, and the run would never be cancelled.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def plugin_server_url() -> str:
    """Return the base URL of the plugin server on the loopback address."""
    from config import USER_PLUGIN_SERVER_PORT

    return f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}"


def _default_entry_timeout() -> float | None:
    from plugin.settings import PLUGIN_EXECUTION_TIMEOUT

    return PLUGIN_EXECUTION_TIMEOUT


# ---------------------------------------------------------------------------
# Timeouts (same rules as the analyzer's executor)
# ---------------------------------------------------------------------------


def _normalize_timeout(value: Any) -> Any:
    """Return ``_TIMEOUT_UNSET`` for a missing or invalid value, None for "no limit"."""
    if value is _TIMEOUT_UNSET:
        return _TIMEOUT_UNSET
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return _TIMEOUT_UNSET
    return seconds if seconds > 0 else None


def resolve_entry_timeout(plugin: dict[str, Any] | None, entry_id: str | None, default: float | None) -> float | None:
    """Return the timeout an entry declares, or ``default`` when it declares none."""
    if not isinstance(plugin, dict):
        return default
    entries = plugin.get("entries")
    if not isinstance(entries, list):
        return default
    target = entry_id or "run"
    for item in entries:
        if not isinstance(item, dict) or item.get("id") != target:
            continue
        resolved = _normalize_timeout(item.get("timeout", _TIMEOUT_UNSET))
        if resolved is not _TIMEOUT_UNSET:
            return resolved
        break
    return default


def _ctx_entry_timeout(ctx: Any, fallback: float | None) -> float | None:
    if isinstance(ctx, dict):
        resolved = _normalize_timeout(ctx.get("entry_timeout", _TIMEOUT_UNSET))
        if resolved is not _TIMEOUT_UNSET:
            return resolved
    return fallback


def run_wait_timeout(entry_timeout: float | None) -> float | None:
    """Return how long the caller waits for a run with this entry timeout."""
    if entry_timeout is None:
        return None
    return max(entry_timeout + 15.0, 315.0)


# ---------------------------------------------------------------------------
# Reading a result (same rules as the user-plugin channel)
# ---------------------------------------------------------------------------


def plugin_terminal_status(success: bool, run_data: Any) -> str:
    """Return ``completed``, ``blocked`` or ``failed`` for a finished run.

    A run that failed stays failed whatever its data says. A run that
    succeeded can report otherwise through its data: ``status == "error"``
    means failed; ``needs_confirmation``, ``action == "clarify"`` or a status
    of ``blocked`` / ``clarify`` / ``confirm_required`` means blocked.
    ``observation_only`` switches these overrides off, and ``executed: false``
    alone blocks nothing.
    """
    if not success:
        return "failed"
    if isinstance(run_data, dict) and not bool(run_data.get("observation_only")):
        status = str(run_data.get("status") or "").strip().lower()
        action = str(run_data.get("action") or "").strip().lower()
        if status == "error":
            return "failed"
        if (
            bool(run_data.get("needs_confirmation"))
            or action == "clarify"
            or status in {"blocked", "clarify", "confirm_required"}
        ):
            return "blocked"
    return "completed"


def resolve_delivery_mode(result: Any) -> str:
    """Return ``proactive``, ``passive`` or ``silent`` from ``meta.agent``.

    ``delivery`` decides when it is present, valid or not: an invalid value
    means proactive and does not fall back to the old ``reply`` flag.
    """
    if not isinstance(result, dict):
        return "proactive"
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return "proactive"
    agent = meta.get("agent")
    if not isinstance(agent, dict):
        return "proactive"
    if "delivery" in agent:
        raw = agent["delivery"]
        if isinstance(raw, str) and raw in ("proactive", "passive", "silent"):
            return raw
        if isinstance(raw, bool):
            return "proactive" if raw else "silent"
        return "proactive"
    reply = agent.get("reply")
    if isinstance(reply, bool):
        return "proactive" if reply else "silent"
    return "proactive"


def _positive_finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if 0.0 < seconds < float("inf") else None


def resolve_result_contract(entry: dict[str, Any] | None, result: Any) -> tuple[str, float | None]:
    """Return ``(result_kind, expires_in_s)``; the result wins over the entry's metadata.

    ``result_kind`` is ``task_result`` unless one side says ``event``, and an
    expiry is kept only for an event.
    """
    static_meta = (entry or {}).get("metadata")
    if not isinstance(static_meta, dict):
        static_meta = {}
    runtime_agent: dict[str, Any] = {}
    if isinstance(result, dict):
        result_meta = result.get("meta")
        if isinstance(result_meta, dict) and isinstance(result_meta.get("agent"), dict):
            runtime_agent = result_meta["agent"]

    result_kind = "task_result"
    for raw_kind in (runtime_agent.get("result_kind"), static_meta.get("result_kind")):
        if isinstance(raw_kind, str) and raw_kind.strip() in ("task_result", "event"):
            result_kind = raw_kind.strip()
            break

    expires_in_s = None
    for raw_expiry in (runtime_agent.get("expires_in_s"), static_meta.get("expires_in_s")):
        expires_in_s = _positive_finite_seconds(raw_expiry)
        if expires_in_s is not None:
            break
    if result_kind != "event":
        expires_in_s = None
    return result_kind, expires_in_s


def llm_result_fields(entry: dict[str, Any] | None) -> list[str] | None:
    """Return the ``llm_result_fields`` an entry declares, or None when it declares none.

    ``GET /plugins`` lists ``[]`` for an entry that declares no fields (and the
    SDK turns an empty declaration into none), so an empty list means none.
    """
    fields = (entry or {}).get("llm_result_fields")
    return list(fields) if isinstance(fields, list) and fields else None


def describe_result(entry: dict[str, Any] | None, outcome: "RunOutcome", *, lang: str | None = None) -> str:
    """Return the text a model is given for a finished run."""
    from utils.result_parser import parse_plugin_result

    error = (outcome.run_error or outcome.error) if not outcome.success else None
    return parse_plugin_result(
        outcome.data,
        llm_result_fields=llm_result_fields(entry),
        plugin_message=str(outcome.message or ""),
        error=error,
        lang=lang,
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class PluginCatalog:
    """The plugin list, fetched from the plugin server and cached.

    The cache is reused until it is ``CATALOG_TTL_SECONDS`` old, until
    ``change_token`` returns a different value, or until ``invalidate`` is
    called (plugin lifecycle events do that). A failed fetch keeps the last
    list that was fetched.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        change_token: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url
        self._client_factory = client_factory or _default_client_factory
        self._change_token = change_token
        self._clock = clock
        self._all: list[dict[str, Any]] = []
        self._fetched_at: float | None = None
        self._fetched_token: Any = None
        self._stale = True
        self._lock = asyncio.Lock()
        self.fetches = 0

    def invalidate(self) -> None:
        """Make the next read fetch again. Safe to call from any thread."""
        self._stale = True

    def _read_token(self) -> Any:
        if self._change_token is None:
            return None
        try:
            return self._change_token()
        except Exception:  # noqa: BLE001 - an unreadable token only costs a refetch
            return object()

    def _fresh(self) -> bool:
        if self._stale or self._fetched_at is None:
            return False
        if self._clock() - self._fetched_at >= CATALOG_TTL_SECONDS:
            return False
        return self._change_token is None or self._read_token() == self._fetched_token

    async def _fetch(self) -> None:
        token = self._read_token()
        base = self._base_url or plugin_server_url()
        self.fetches += 1
        try:
            async with self._client_factory(
                timeout=CATALOG_FETCH_TIMEOUT_SECONDS, proxy=None, trust_env=False
            ) as client:
                response = await client.get(f"{base}/plugins")
                if response.status_code != 200:
                    logger.debug("plugin list returned HTTP %s; keeping the cached list", response.status_code)
                    return
                payload = response.json()
        except Exception as exc:  # noqa: BLE001 - the cached list stays in use
            logger.debug("plugin list fetch failed (%s); keeping the cached list", type(exc).__name__)
            return
        raw = payload.get("plugins") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return
        plugins: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            entries = item.get("entries")
            if isinstance(entries, list):
                # Tools registered for the main dialogue are not plugin entries.
                item["entries"] = [
                    entry
                    for entry in entries
                    if not (
                        isinstance(entry, dict)
                        and isinstance(entry.get("id"), str)
                        and entry["id"].startswith(_LLM_TOOL_ENTRY_PREFIX)
                    )
                ]
            plugins.append(item)
        self._all = plugins
        self._fetched_at = self._clock()
        self._fetched_token = token
        self._stale = False

    async def installed(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return every plugin the server lists, running or not."""
        async with self._lock:
            if force_refresh or not self._fresh():
                await self._fetch()
            return list(self._all)

    async def plugins(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return the running plugins, which are the ones that can be called."""
        return [plugin for plugin in await self.installed(force_refresh=force_refresh) if plugin.get("status") == "running"]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """How one call of a plugin entry ended."""

    plugin_id: str
    entry_id: str | None
    # False when no run was created (unknown plugin or entry, server unreachable).
    accepted: bool
    success: bool
    # The run's last status: one of ``RUN_TERMINAL_STATUSES``, or "unknown".
    status: str | None = None
    run_id: str | None = None
    run_token: str | None = None
    expires_at: Any = None
    # ``data`` and ``meta`` of the plugin's response envelope.
    data: Any = None
    meta: Any = None
    # The run's own message (its last progress message; "done" by default).
    message: str | None = None
    error: str | None = None
    run_error: str | None = None
    reason: str = ""
    progress: Any = None
    stage: Any = None

    @property
    def envelope(self) -> dict[str, Any]:
        """Return the part of the result the delivery rules read."""
        return {"meta": self.meta}


class PluginRunner:
    """Sends ``/runs`` requests and follows them to their end."""

    def __init__(
        self,
        catalog: PluginCatalog,
        *,
        base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        default_entry_timeout: Callable[[], float | None] = _default_entry_timeout,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._catalog = catalog
        self._base_url = base_url
        self._client_factory = client_factory or _default_client_factory
        self._default_entry_timeout = default_entry_timeout
        self._poll_interval = poll_interval
        # commission id -> ids of its runs that have not ended yet.
        self._active: dict[str, set[str]] = {}
        # Runs whose polling was given up on before they ended: run id ->
        # commission id. They stay in ``_active`` until a cancel request for
        # them is answered, so stopping the commission still reaches them.
        self._lost: dict[str, str] = {}
        # Runs the plugin server accepted a cancel for, so stopping a
        # commission and dropping its call do not ask the plugin twice. Bounded.
        self._cancel_sent: dict[str, None] = {}
        # Cancel requests on their way, by run id; a second caller waits for the same one.
        self._cancel_pending: dict[str, asyncio.Future[bool]] = {}
        self._cancel_tasks: set[asyncio.Future[Any]] = set()
        # Run requests whose caller went away, by the commission they were
        # for, until the run they created has been handed to a cancel.
        self._abandoned: dict[asyncio.Future[Any], str] = {}

    @property
    def base_url(self) -> str:
        return self._base_url or plugin_server_url()

    def _client(self) -> Any:
        import httpx

        return self._client_factory(timeout=httpx.Timeout(10.0, connect=2.0), proxy=None, trust_env=False)

    def active_runs(self, commission_id: str) -> list[str]:
        """Return the ids of the commission's runs that are still going."""
        return sorted(self._active.get(commission_id, ()))

    # -- cancel -----------------------------------------------------------

    async def cancel_run(self, run_id: str, reason: str = "") -> bool:
        """Ask the plugin server to cancel one run. Returns whether it accepted.

        A run the server accepted a cancel for is not asked again, and a call
        while a request for the run is on its way waits for that request. A
        request that failed is sent again by the next call.
        """
        if run_id in self._cancel_sent:
            return True
        pending = self._cancel_pending.get(run_id)
        if pending is None:
            pending = asyncio.ensure_future(self._send_cancel(run_id, reason))
            self._cancel_pending[run_id] = pending
            self._cancel_tasks.add(pending)

            def forget(done: "asyncio.Future[bool]", run_id: str = run_id) -> None:
                self._cancel_tasks.discard(done)
                if self._cancel_pending.get(run_id) is done:
                    del self._cancel_pending[run_id]

            pending.add_done_callback(forget)
        # A caller that goes away does not take the request down with it.
        return await asyncio.shield(pending)

    async def _send_cancel(self, run_id: str, reason: str) -> bool:
        try:
            async with self._client() as client:
                response = await client.post(f"{self.base_url}/runs/{run_id}/cancel", json={"reason": reason or None})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported; the next call asks again
            logger.warning("cancelling run %s failed: %s", run_id, type(exc).__name__)
            return False
        if response.status_code != 200:
            logger.warning("cancelling run %s returned HTTP %s", run_id, response.status_code)
            if response.status_code in (404, 410):
                # The run is gone: there is nothing left to stop.
                commission_id = self._lost.pop(run_id, None)
                if commission_id is not None:
                    self._untrack(commission_id, run_id)
            return False
        self._cancel_sent[run_id] = None
        while len(self._cancel_sent) > _CANCEL_MEMORY:
            del self._cancel_sent[next(iter(self._cancel_sent))]
        commission_id = self._lost.pop(run_id, None)
        if commission_id is not None:
            self._untrack(commission_id, run_id)
        return True

    def _untrack(self, commission_id: str, run_id: str) -> None:
        runs = self._active.get(commission_id)
        if runs is not None:
            runs.discard(run_id)
            if not runs:
                del self._active[commission_id]

    async def cancel_commission(self, commission_id: str, reason: str = "") -> int:
        """Cancel every run still going under a commission. Returns how many were asked."""
        run_ids = self.active_runs(commission_id)
        if run_ids:
            await asyncio.gather(*(self.cancel_run(run_id, reason) for run_id in run_ids))
        return len(run_ids)

    def _cancel_in_background(self, run_id: str, reason: str) -> None:
        task = asyncio.ensure_future(self.cancel_run(run_id, reason))
        self._cancel_tasks.add(task)
        task.add_done_callback(self._cancel_tasks.discard)

    async def aclose(self) -> None:
        """Wait for cancel requests that are still on their way.

        Also for the ones added while waiting: a run request that comes back
        after its caller went away adds the cancel of the run it created.
        """
        while True:
            # A request that has come back hands its run to a cancel only
            # once its done callbacks run, which may not have happened yet.
            for request in [request for request in self._abandoned if request.done()]:
                self._cancel_abandoned_run(request)
            pending = [task for task in self._cancel_tasks if not task.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    # -- invoke -----------------------------------------------------------

    async def run_entry(
        self,
        *,
        commission_id: str,
        plugin_id: str | None,
        entry_id: str | None = None,
        args: dict[str, Any] | None = None,
        lanlan_name: str | None = None,
        conversation_id: str | None = None,
        latest_user_request: str = "",
        on_progress: ProgressCallback | None = None,
        on_run_created: Callable[[str], Awaitable[None]] | None = None,
        housekeeping: bool = False,
    ) -> RunOutcome:
        """Call one plugin entry and wait for the run to end.

        Never raises for a failed call; the outcome says what happened. When
        the caller is cancelled while waiting, the run is cancelled on the
        plugin server as well.

        ``housekeeping`` is for a call the host makes on its own account and
        no model chose (handing a deferred task's id to the plugin's
        ``bind_task`` entry): such an entry may be hidden from selection, so
        the entry is looked for among all of the plugin's entries.
        """
        plugin_args = dict(args) if isinstance(args, dict) else {}
        requested_entry = entry_id or plugin_args.pop("_entry", None)

        def rejected(error: str, reason: str, entry: Any = requested_entry) -> RunOutcome:
            return RunOutcome(
                plugin_id=str(plugin_id or ""),
                entry_id=None if entry is None else str(entry),
                accepted=False,
                success=False,
                error=error,
                reason=reason,
            )

        if not plugin_id:
            return rejected("No plugin_id provided", "plugin_id_missing")

        def find(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
            return next((item for item in candidates if item.get("id") == plugin_id), None)

        plugin = find(await self._catalog.plugins())
        if plugin is None:
            # The list may be older than the selection that picked this plugin.
            plugin = find(await self._catalog.plugins(force_refresh=True))
        if plugin is None:
            return rejected(f"Plugin {plugin_id} not found", "Plugin not found")

        entries_listed = isinstance(plugin.get("entries"), list)
        listed = (
            [item for item in plugin.get("entries") or [] if isinstance(item, dict)]
            if housekeeping
            else visible_entries(plugin)
        )
        known = [str(item.get("id")) for item in listed if item.get("id")]
        if entries_listed and not known:
            return rejected(f"Plugin {plugin_id} has no Agent-visible entries", "no_agent_visible_entries")
        if entries_listed and not requested_entry:
            return rejected(
                f"entry_id is required for plugin '{plugin_id}'. Available: {known}", "entry_id_missing"
            )
        if isinstance(requested_entry, int):
            requested_entry = str(requested_entry)
        if requested_entry and known and requested_entry not in known:
            # The only tolerance: the same id in a different case.
            matches = [item for item in known if item.lower() == str(requested_entry).lower()]
            if len(matches) == 1:
                requested_entry = matches[0]
            elif not matches:
                return rejected(
                    f"entry_id '{requested_entry}' not found in plugin '{plugin_id}'. Available: {known}",
                    "invalid_entry_id",
                )
            else:
                # Sending the id as it was asked for could reach a hidden
                # entry of exactly that name.
                return rejected(
                    f"entry_id '{requested_entry}' is ambiguous in plugin '{plugin_id}': it matches {matches}",
                    "invalid_entry_id",
                )

        resolved_entry = str(requested_entry or "run")
        safe_args = dict(plugin_args)
        entry_timeout = resolve_entry_timeout(plugin, requested_entry, self._default_entry_timeout())
        try:
            ctx = safe_args.get("_ctx")
            ctx = dict(ctx) if isinstance(ctx, dict) else {}
            if lanlan_name and "lanlan_name" not in ctx:
                ctx["lanlan_name"] = lanlan_name
            if conversation_id:
                ctx["conversation_id"] = conversation_id
            # The user's own words, for entries whose arguments the model
            # rewrote. A value the arguments already carry is kept.
            if latest_user_request:
                ctx.setdefault("latest_user_request", latest_user_request)
            entry_timeout = _ctx_entry_timeout(ctx, entry_timeout)
            ctx["entry_timeout"] = entry_timeout
            safe_args["_ctx"] = ctx
        except Exception as exc:  # noqa: BLE001 - a call without _ctx is better than no call
            logger.warning("could not build _ctx for %s.%s: %s", plugin_id, resolved_entry, type(exc).__name__)

        body = {
            "task_id": commission_id,
            "plugin_id": plugin_id,
            "entry_id": resolved_entry,
            "args": safe_args,
        }

        def failed(error: str, reason: str) -> RunOutcome:
            return RunOutcome(
                plugin_id=plugin_id,
                entry_id=resolved_entry,
                accepted=False,
                success=False,
                error=error,
                reason=reason,
            )

        # The request runs as a task of its own. Once it is sent the run may
        # exist on the plugin server, so a caller that is cancelled meanwhile
        # must not take the request down with it: the answer is still read,
        # and the run it names is cancelled.
        request = asyncio.ensure_future(self._create_run(body))
        self._cancel_tasks.add(request)
        request.add_done_callback(self._cancel_tasks.discard)
        try:
            created, problem = await asyncio.shield(request)
        except asyncio.CancelledError:
            self._abandoned[request] = commission_id
            request.add_done_callback(self._cancel_abandoned_run)
            raise
        if problem is not None:
            return failed(*problem)

        run_id = created.get("run_id") if isinstance(created, dict) else None
        run_token = created.get("run_token") if isinstance(created, dict) else None
        expires_at = created.get("expires_at") if isinstance(created, dict) else None
        if not isinstance(run_id, str) or not run_id or not isinstance(run_token, str) or not run_token:
            return failed("Invalid /runs response (missing run_id/run_token)", "run_invalid_response")

        self._active.setdefault(commission_id, set()).add(run_id)
        lost = False
        try:
            if on_run_created is not None:
                try:
                    await on_run_created(run_id)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a listener never costs the run its result
                    logger.warning("the listener for new runs failed", exc_info=True)
            try:
                completion = await self._await_completion(
                    run_id, timeout=run_wait_timeout(entry_timeout), on_progress=on_progress
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                logger.warning("waiting for run %s failed: %s", run_id, type(exc).__name__)
                completion = {"status": "unknown", "success": False, "data": None, "error": str(exc)}
            lost = bool(completion.pop("lost", False))
        except asyncio.CancelledError:
            # Whoever waited for this run is gone, at whichever point after
            # the run came to exist; the run must not go on. Until the plugin
            # server accepts the cancel the run stays known to the commission,
            # so stopping the commission asks again if this request fails.
            if run_id not in self._cancel_sent:
                self._lost[run_id] = commission_id
                lost = True
            self._cancel_in_background(run_id, "caller cancelled")
            raise
        finally:
            if not lost:
                self._untrack(commission_id, run_id)
        if lost:
            # Nobody follows the run any more, and it may still be going: it
            # is cancelled, and stays known to the commission until the plugin
            # server accepts that, so stopping the commission asks again.
            self._lost[run_id] = commission_id
            self._cancel_in_background(run_id, "polling failed")

        success = bool(completion.get("success"))
        return RunOutcome(
            plugin_id=plugin_id,
            entry_id=resolved_entry,
            accepted=True,
            success=success,
            status=completion.get("status"),
            run_id=run_id,
            run_token=run_token,
            expires_at=expires_at,
            data=completion.get("data"),
            meta=completion.get("meta"),
            message=completion.get("message"),
            error=None if success else completion.get("error"),
            run_error=completion.get("run_error", completion.get("error")),
            reason="run_succeeded" if success else "run_failed",
            progress=completion.get("progress"),
            stage=completion.get("stage"),
        )

    async def _create_run(self, body: dict[str, Any]) -> tuple[Any, tuple[str, str] | None]:
        """Send ``POST /runs``. Returns ``(answer, None)`` or ``(None, (error, reason))``."""
        target = f"{body.get('plugin_id')}.{body.get('entry_id')}"
        try:
            async with self._client() as client:
                response = await client.post(f"{self.base_url}/runs", json=body)
            if not 200 <= response.status_code < 300:
                logger.warning("/runs returned HTTP %s for %s", response.status_code, target)
                return None, (f"/runs returned {response.status_code}", "run_failed")
            try:
                return response.json(), None
            except Exception:  # noqa: BLE001 - never retried: the run may exist
                return None, ("Invalid /runs response (non-JSON)", "run_invalid_response")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            logger.warning("/runs request failed for %s: %s", target, type(exc).__name__)
            return None, (str(exc) or type(exc).__name__, "run_failed")

    def _cancel_abandoned_run(self, request: "asyncio.Future[Any]") -> None:
        """Cancel the run a request created after its caller had gone away.

        The run is known to the commission until the plugin server accepts the
        cancel, so stopping the commission asks again if this request fails.
        Runs once per request, from its done callback or from ``aclose``,
        whichever comes first.
        """
        commission_id = self._abandoned.pop(request, None)
        if commission_id is None:
            return
        if request.cancelled() or request.exception() is not None:
            return
        created, _problem = request.result()
        run_id = created.get("run_id") if isinstance(created, dict) else None
        if isinstance(run_id, str) and run_id:
            if run_id not in self._cancel_sent:
                self._active.setdefault(commission_id, set()).add(run_id)
                self._lost[run_id] = commission_id
            self._cancel_in_background(run_id, "caller cancelled")

    async def _await_completion(
        self,
        run_id: str,
        *,
        timeout: float | None,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        """Poll the run until it is terminal, then read its exported result."""
        base = self.base_url
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        last_status: str | None = None
        last_progress: tuple[Any, ...] | None = None
        consecutive_errors = 0
        record: dict[str, Any] = {}

        async with self._client() as client:
            while True:
                # Also when the request below came back as if nothing had happened.
                _reraise_swallowed_cancellation()
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    return {
                        "status": "timeout",
                        "success": False,
                        "data": None,
                        "error": f"Timed out waiting for run {run_id} ({timeout}s)",
                    }
                try:
                    response = await client.get(f"{base}/runs/{run_id}")
                    if response.status_code in (404, 410):
                        return {
                            "status": "failed",
                            "success": False,
                            "data": None,
                            "error": f"Run {run_id} not found (HTTP {response.status_code})",
                        }
                    if response.status_code != 200:
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                            return {
                                "status": "failed",
                                "success": False,
                                "data": None,
                                "error": (
                                    f"Run {run_id} polling failed "
                                    f"({consecutive_errors} consecutive HTTP {response.status_code})"
                                ),
                                "lost": True,
                            }
                    else:
                        parsed = response.json()
                        if not isinstance(parsed, dict):
                            raise ValueError("the run record is not an object")
                        # Only an answer that could be read counts as one.
                        consecutive_errors = 0
                        record = parsed
                        last_status = record.get("status")
                        if on_progress is not None and last_status not in RUN_TERMINAL_STATUSES:
                            key = (record.get("progress"), record.get("stage"), record.get("message"), record.get("step"))
                            if key != last_progress:
                                last_progress = key
                                try:
                                    await on_progress(
                                        progress=record.get("progress"),
                                        stage=record.get("stage"),
                                        message=record.get("message"),
                                        step=record.get("step"),
                                        step_total=record.get("step_total"),
                                    )
                                except Exception:  # noqa: BLE001 - a listener never fails the run
                                    logger.debug("progress listener failed", exc_info=True)
                        if last_status in RUN_TERMINAL_STATUSES:
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - counted; three in a row end the wait
                    _reraise_swallowed_cancellation()
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                        logger.warning("polling run %s failed: %s", run_id, type(exc).__name__)
                        return {
                            "status": "failed",
                            "success": False,
                            "data": None,
                            "error": (
                                f"Run {run_id} polling failed "
                                f"({consecutive_errors} consecutive transport errors)"
                            ),
                            "lost": True,
                        }
                pause = self._poll_interval if remaining is None else min(self._poll_interval, remaining)
                await asyncio.sleep(pause)

            result: dict[str, Any] = {
                "status": last_status,
                "success": last_status == "succeeded",
                "data": None,
                "error": None,
                "progress": record.get("progress"),
                "stage": record.get("stage"),
                "message": record.get("message"),
            }
            if last_status in ("failed", "canceled", "timeout"):
                error = record.get("error")
                if isinstance(error, dict):
                    result["error"] = error.get("message") or str(error.get("code") or "unknown")
                elif isinstance(error, str):
                    result["error"] = error
                else:
                    result["error"] = f"Run {last_status}"

            try:
                response = await client.get(f"{base}/runs/{run_id}/export", params={"limit": 50})
                if response.status_code == 200:
                    for item in response.json().get("items") or []:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "json" and (
                            item.get("json") is not None or item.get("json_data") is not None
                        ):
                            raw = item.get("json") or item.get("json_data")
                            if isinstance(raw, dict):
                                result["data"] = raw.get("data")
                                result["meta"] = raw.get("meta")
                                if raw.get("error"):
                                    error = raw["error"]
                                    if isinstance(error, dict):
                                        result["error"] = error.get("message") or str(error)
                                    elif isinstance(error, str):
                                        result["error"] = error
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the run's status still stands
                _reraise_swallowed_cancellation()
                logger.debug("export of run %s could not be read: %s", run_id, type(exc).__name__)
            return result

"""Process-global registry for plugin-owned LLM tools.

Plugins use ``ctx.register_llm_tool`` (SDK-side, see
``plugin/sdk/plugin/llm_tool.py``) to expose model-callable tools at
runtime. The plugin process emits an ``LLM_TOOL_REGISTER`` IPC message;
the host (``user_plugin_server``) handler in
``plugin/core/communication.py`` consumes that message and calls
:func:`register_remote_tool` here, which:

1. Tracks ``(plugin_id, tool_name)`` so we can mass-clear on plugin
   shutdown without leaking tools onto ``main_server``'s registry.
2. POSTs to ``main_server`` ``/api/tools/register`` with a callback URL
   pointing back at this process — the route at
   ``plugin/server/routes/llm_tools.py`` then routes incoming model
   dispatches into the right plugin via ``host.trigger`` IPC.

The actual handler executes inside the plugin's child process. We never
import or call user code in this module — registration here is pure
metadata + an HTTP round-trip.

Loopback constraint
-------------------
``main_server`` validates every ``callback_url`` is loopback-only (see
``main_routers/tool_router.py::_validate_local_callback_url``). We
therefore always build URLs from ``127.0.0.1`` and the actually-bound
``user_plugin_server`` port (which may differ from the configured
default if 48916 was busy at boot — see ``plugin/user_plugin_server.py``
where ``NEKO_USER_PLUGIN_SERVER_PORT`` is overwritten with the
selected port).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, NoReturn, Optional, Tuple, TYPE_CHECKING
from uuid import uuid4

import psutil

if TYPE_CHECKING:
    import httpx

from config import MAIN_SERVER_PORT, USER_PLUGIN_SERVER_PORT
from plugin.logging_config import get_logger
from plugin.utils.http_imports import ensure_httpx, load_httpx

logger = get_logger("server.messaging.llm_tool_registry")


# ---------------------------------------------------------------------------
# Process-global state
# ---------------------------------------------------------------------------

# plugin_id -> {tool_name -> {"timeout_seconds": float}}.
# Used to wipe everything on plugin stop without iterating main_server's
# registry, to detect duplicate registrations, and to look up the
# per-tool timeout in the callback route (so we don't hard-code 30s).
_plugin_tools: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
_lock = asyncio.Lock()

# (plugin_id, tool_name) -> [lock, number of holders and waiters].
# Serialises register / unregister of one name of one plugin across the
# main_server call *and* the tracking update; ``_lock`` alone only guards
# the dict. Without it, an unregister whose SDK caller was cancelled can
# answer after a newer register of the same name has completed and pop the
# tracking that register wrote. Entries are dropped as soon as nobody
# holds or waits for them, so the map stays bounded by the operations in
# flight (nothing is left behind once a plugin has stopped).
_name_locks: Dict[Tuple[str, str], List[Any]] = {}

# plugin_id -> number of times ``clear_plugin_tools`` ran for it (plugin
# stops). A register snapshots this when it starts; a different value once
# main_server answered means the plugin was stopped (and its tools cleared)
# while the register was in flight, so the register must not survive.
# Only plugin stop bumps it, never plugin start: registers of a starting
# plugin (``@llm_tool`` at startup included) begin after the stop that
# preceded the start has finished, so they snapshot the bumped value and
# are never mistaken for leftovers. Bounded by the number of plugins.
_stop_generations: Dict[str, int] = defaultdict(int)

# Budget for a compensating / retried call to main_server after an
# interrupted register or unregister. It runs under the per-name lock and,
# when the original call was cancelled, delays that cancellation, so keep
# it short (main_server is on loopback; a normal answer takes milliseconds).
_RECONCILE_TIMEOUT_SECONDS = 2.0


@contextlib.asynccontextmanager
async def _tool_name_lock(plugin_id: str, name: str) -> AsyncIterator[None]:
    """Hold the per-name lock of ``(plugin_id, name)``.

    Lock order: this lock is taken first and ``_lock`` only inside it, for
    the tracking update; ``_lock`` is never held while waiting for this one.
    ``clear_plugin_tools`` does not take it, so plugin-stop cleanup never
    waits behind an in-flight register / unregister.
    """
    key = (plugin_id, name)
    # No await between lookup and increment: all callers share one loop
    # (as the shared httpx client already requires).
    entry = _name_locks.get(key)
    if entry is None:
        entry = [asyncio.Lock(), 0]
        _name_locks[key] = entry
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0 and _name_locks.get(key) is entry:
            del _name_locks[key]


async def _finish_despite_cancellation(coro: Any) -> Tuple["asyncio.Future[Any]", bool]:
    """Run ``coro`` to the end even if the caller is cancelled meanwhile.

    Returns the finished task (the caller reads its result / exception) and
    whether a cancellation of the caller arrived while waiting; the caller
    then re-raises ``CancelledError`` itself. ``coro`` must be bounded in
    time (the reconcile calls carry ``_RECONCILE_TIMEOUT_SECONDS``), since
    cancellation is held back until it ends.
    """
    task = asyncio.ensure_future(coro)
    interrupted = False
    while not task.done():
        try:
            # ``asyncio.wait`` does not cancel ``task`` when we are cancelled.
            await asyncio.wait({task})
        except asyncio.CancelledError:
            interrupted = True
    return task, interrupted


@dataclass
class _ToolHostInstance:
    instance_id: str
    pid: int
    started_at: float
    registration_attempted: bool = False


_tool_host_instance: _ToolHostInstance | None = None


def tool_host_instance_id() -> str | None:
    host = _tool_host_instance
    return host.instance_id if host is not None else None


async def start_tool_host_instance() -> str:
    global _tool_host_instance
    started_at = await asyncio.to_thread(lambda: psutil.Process().create_time())
    host = _ToolHostInstance(uuid4().hex, os.getpid(), started_at)
    _tool_host_instance = host
    return host.instance_id


async def release_tool_host_instance(host_instance_id: str) -> None:
    host = _tool_host_instance
    if host is None or host.instance_id != host_instance_id or not host.registration_attempted:
        return

    # This runs on the HTTP server loop, which may differ from the IPC loop.
    httpx = await ensure_httpx()
    try:
        from utils.internal_http_client import internal_service_event_hooks

        async with httpx.AsyncClient(
            timeout=0.5,
            trust_env=False,
            event_hooks=internal_service_event_hooks(),
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    f"{_main_server_base_url()}/api/tools/release-host",
                    json={"host_instance_id": host_instance_id},
                ),
                timeout=0.5,
            )
            response.raise_for_status()
    except (TimeoutError, httpx.HTTPError) as exc:
        logger.debug(
            "Tool host {} release failed; main_server will probe it: {}", host_instance_id, exc,
        )


# Lazy-initialised httpx client. We share one client across registrations
# because the plugin server may register dozens of tools at startup —
# creating one client per call would balloon TCP connections.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        httpx = load_httpx()
        from utils.internal_http_client import internal_service_event_hooks

        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=2.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            proxy=None,
            trust_env=False,
            event_hooks=internal_service_event_hooks(),
        )
    return _HTTP_CLIENT


async def shutdown_http_client() -> None:
    """Close the shared httpx client; called from server lifespan teardown."""
    global _HTTP_CLIENT
    client = _HTTP_CLIENT
    _HTTP_CLIENT = None
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:
            logger.debug("llm_tool_registry http client close failed: {}", exc)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _resolve_user_plugin_port() -> int:
    """Return the actually-bound user_plugin_server port.

    ``user_plugin_server.py`` sets ``NEKO_USER_PLUGIN_SERVER_PORT`` to the
    *selected* port at boot (which may not equal the configured default
    if the default was busy). Prefer that env var; fall back to the
    config default if we are running embedded inside ``agent_server``
    (where the env var is set by the host launcher).
    """
    raw = os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "NEKO_USER_PLUGIN_SERVER_PORT env var is not an int: {!r}, falling back to config default",
                raw,
            )
    return int(USER_PLUGIN_SERVER_PORT)


def build_callback_url(plugin_id: str, tool_name: str) -> str:
    """Build the loopback callback URL ``main_server`` POSTs to when the
    LLM picks this plugin's tool.

    Path components are inlined into the URL. ``plugin_id`` and ``tool_name``
    must already be sanitised (the SDK enforces this with a regex check
    before sending the IPC message).
    """
    port = _resolve_user_plugin_port()
    return f"http://127.0.0.1:{port}/api/llm-tools/callback/{plugin_id}/{tool_name}"


def _main_server_base_url() -> str:
    return f"http://127.0.0.1:{int(MAIN_SERVER_PORT)}"


def _source_tag(plugin_id: str) -> str:
    """``main_server`` keys ``/api/tools/clear`` requests by ``source``;
    we tag every plugin-owned tool with ``plugin:{plugin_id}`` so a
    single clear call wipes all of a plugin's tools at once."""
    return f"plugin:{plugin_id}"


# ---------------------------------------------------------------------------
# Request parsing + typed failures (shared by the IPC notification handlers in
# ``plugin/core/communication.py`` and the request-response handlers in
# ``plugin/server/messaging/handlers/llm_tools.py``)
# ---------------------------------------------------------------------------


class RemoteToolError(RuntimeError):
    """``main_server`` answered, but the request did not (fully) succeed.

    ``code`` is one of:

    * ``"main_server_error"`` -- HTTP status >= 400 (e.g. unknown role).
    * ``"rejected"`` -- register: no role accepted the tool (``ok: false``).
    * ``"partial_failure"`` -- unregister: some roles reported ``failed_roles``.
    * ``"timeout"`` -- the answer is not a JSON object: main_server may have
      applied the change, so the outcome is unknown (the SDK then cleans up
      after a register, as for a timeout).
    * ``"plugin_stopped"`` -- register: the plugin was stopped meanwhile;
      nothing is tracked, and anything main_server may have stored was
      withdrawn by registration id on a best-effort basis (the message says
      whether the withdrawal was confirmed). ``body`` is empty once withdrawn.

    ``body`` is the parsed JSON body when there is one, so callers can report
    the per-role outcome instead of a flattened message. Subclasses
    ``RuntimeError`` so existing ``except RuntimeError`` callers keep working.
    """

    def __init__(self, message: str, *, code: str, body: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.body: Dict[str, Any] = body if isinstance(body, dict) else {}


def _valid_tool_name(name: object) -> bool:
    """Same rule the SDK enforces in the plugin process. Checked again here
    because the name is inlined into the callback URL path (``/`` and other
    separators are refused, so it stays one segment under this plugin's own
    prefix; names made only of dots are refused because the path would
    normalise them away), and the plugin process is not trusted to have run
    the SDK check. Used by both parsers, so the notification handlers and the
    request-response handlers apply the same rule."""
    from plugin.sdk.plugin.llm_tool import validate_tool_name

    if not isinstance(name, str):
        return False
    try:
        validate_tool_name(name)
    except ValueError:
        return False
    return True


_REGISTRATION_ID_MAX_LENGTH = 64


def failed_before_send(httpx: Any, exc: BaseException) -> bool:
    """Whether an httpx error from a main_server call certainly happened
    before the request was sent (connect failure or timeout, no pooled
    connection, proxy, unsupported scheme, invalid request on our side).
    Anything else (read/write timeout or failure, connection dropped
    mid-exchange, undecodable answer, ...) means main_server may have
    applied the change: the outcome is unknown."""
    return isinstance(exc, (
        httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
        httpx.ProxyError, httpx.UnsupportedProtocol, httpx.LocalProtocolError,
    ))


def _registration_id(value: object) -> Optional[str]:
    """A caller-supplied registration id, or ``None`` when absent/unusable
    (main_server then generates one / applies no registration filter)."""
    if isinstance(value, str) and 0 < len(value) <= _REGISTRATION_ID_MAX_LENGTH:
        return value
    return None


def parse_register_request(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise an ``LLM_TOOL_REGISTER`` payload into ``register_remote_tool``
    keyword arguments (minus ``plugin_id``). Returns ``None`` when the tool
    name or a given ``registration_id`` is missing or invalid."""
    name = msg.get("name")
    if not _valid_tool_name(name):
        return None
    raw_id = msg.get("registration_id")
    if raw_id is not None and _registration_id(raw_id) is None:
        # Refuse, like an invalid expected_registration_id: dropping it would
        # store a generated id the caller does not know, so its later
        # targeted unregister / cleanup could never match.
        return None
    parameters = msg.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    timeout_seconds = msg.get("timeout_seconds")
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        timeout_seconds = 30.0
    role = msg.get("role")
    if role is not None and not isinstance(role, str):
        role = None
    return {
        "name": name,
        "description": str(msg.get("description") or ""),
        "parameters": parameters,
        "timeout_seconds": float(timeout_seconds),
        "role": role,
        **({"reply_tail": True} if msg.get("reply_tail") is True else {}),
        **({"registration_id": rid} if (rid := _registration_id(msg.get("registration_id"))) else {}),
    }


def parse_unregister_request(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise an ``LLM_TOOL_UNREGISTER`` payload into
    ``unregister_remote_tool`` keyword arguments (minus ``plugin_id``).
    Returns ``None`` when the tool name or a given
    ``expected_registration_id`` is missing or invalid."""
    name = msg.get("name")
    if not _valid_tool_name(name):
        return None
    raw_expected = msg.get("expected_registration_id")
    if raw_expected is not None and _registration_id(raw_expected) is None:
        # Refuse rather than drop it: without the filter the unregister
        # would remove any registration of the plugin (fail-open).
        return None
    role = msg.get("role")
    if role is not None and not isinstance(role, str):
        role = None
    return {
        "name": name,
        "role": role,
        **(
            {"expected_registration_id": rid}
            if (rid := _registration_id(msg.get("expected_registration_id"))) else {}
        ),
    }


# ---------------------------------------------------------------------------
# main_server API wrappers
# ---------------------------------------------------------------------------


async def register_remote_tool(
    *,
    plugin_id: str,
    name: str,
    description: str,
    parameters: Dict[str, Any],
    timeout_seconds: float,
    role: Optional[str] = None,
    reply_tail: bool = False,
    registration_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a plugin-owned LLM tool with ``main_server``.

    Idempotent at the registry level: if the same ``(plugin_id, name)``
    is registered twice, the second call still POSTs to ``main_server``
    (which uses ``replace=True`` semantics in
    ``register_tool_and_sync``) and overwrites the first.

    Returns the parsed JSON body from ``main_server``. Raises any
    httpx error (main_server unreachable) and :class:`RemoteToolError`
    when main_server answered with an error status or ``ok: false``, so
    the caller can surface the outcome back to the plugin via IPC.

    Runs under the per-name lock (see ``_tool_name_lock``), so it does not
    interleave with another register / unregister of the same name.

    Interrupted registers are reconciled under that same lock:

    * The plugin was stopped (``clear_plugin_tools`` ran) after this call
      started: nothing is sent if the stop came first; otherwise the
      registration main_server may have stored is withdrawn by its id
      (best effort: if that fails it stays until the plugin's next
      cleanup), nothing is tracked, and
      ``RemoteToolError(code="plugin_stopped")`` is raised, also when the
      register itself failed (chained from that error).
    * The call is cancelled after the request was sent, or its answer was
      lost while the plugin runs (an httpx error after sending, see
      ``failed_before_send``, or an unreadable answer, ``code="timeout"``):
      main_server may still store the tool, so the registration is removed
      by its id before the cancellation / error propagates. If that removal
      fails while the plugin is still running, this registration is tracked
      anyway (replacing an earlier entry of the name, with this register's
      ``timeout_seconds``), so model calls reaching it are routed rather
      than answered with "not registered". The error is raised unchanged;
      an SDK caller then sends its own cleanup by the same id, which is
      harmless after the host's withdrawal (``removed: false``).
    """
    # Known before sending, so an interrupted register can be removed by
    # exactly this id and never a newer registration of the same name.
    # The decorator / sync SDK paths send none; main_server would otherwise
    # generate one we might never learn.
    registration_id = registration_id or uuid4().hex
    generation = _stop_generations.get(plugin_id, 0)
    async with _tool_name_lock(plugin_id, name):
        if _stop_generations.get(plugin_id, 0) != generation:
            raise RemoteToolError(
                f"plugin '{plugin_id}' was stopped before '{name}' could be registered",
                code="plugin_stopped",
            )
        return await _register_remote_tool_locked(
            plugin_id=plugin_id, name=name, description=description, parameters=parameters,
            timeout_seconds=timeout_seconds, role=role, reply_tail=reply_tail,
            registration_id=registration_id, generation=generation,
        )


async def _register_remote_tool_locked(
    *,
    plugin_id: str,
    name: str,
    description: str,
    parameters: Dict[str, Any],
    timeout_seconds: float,
    role: Optional[str],
    reply_tail: bool,
    registration_id: str,
    generation: int,
) -> Dict[str, Any]:
    callback_url = build_callback_url(plugin_id, name)
    httpx = await ensure_httpx()
    host = _tool_host_instance
    payload = {
        "name": name,
        "description": description,
        "parameters": parameters,
        "callback_url": callback_url,
        "role": role,
        "source": _source_tag(plugin_id),
        "timeout_seconds": float(timeout_seconds),
        **({"reply_tail": True} if reply_tail else {}),
        "registration_id": registration_id,
    }

    client = _get_http_client()
    url = f"{_main_server_base_url()}/api/tools/register"
    if host is not None:
        payload.update(
            host_instance_id=host.instance_id, host_pid=host.pid,
            host_process_started_at=host.started_at,
        )
        host.registration_attempted = True

    def stopped() -> bool:
        return _stop_generations.get(plugin_id, 0) != generation

    try:
        body = await _post_register(httpx, client, url, payload, plugin_id=plugin_id, name=name)
        async with _lock:
            # Checked together with the write: ``clear_plugin_tools`` bumps
            # the generation and drops tracking under the same lock.
            plugin_stopped = stopped()
            if not plugin_stopped:
                _plugin_tools[plugin_id][name] = {
                    "timeout_seconds": float(timeout_seconds),
                    # Which registration we track; an unregister aimed at an
                    # older one must not drop it (see unregister_remote_tool).
                    "registration_id": body.get("registration_id") or registration_id,
                }
    except asyncio.CancelledError:
        # Possibly stored on main_server, never tracked here.
        await _finish_despite_cancellation(_reconcile_unknown_register(
            plugin_id=plugin_id, name=name, role=role, registration_id=registration_id,
            timeout_seconds=timeout_seconds, stopped=stopped,
            why="register cancelled after it was sent",
        ))
        raise
    except (httpx.HTTPError, RemoteToolError) as exc:
        # After the plugin was stopped, nothing else will clear what this
        # register may have left on main_server: a lost or unreadable answer,
        # ``rejected`` (main_server records the tool before syncing roles, so
        # ``ok: false`` after every role's sync failed still leaves it
        # stored), or anything unexpected. A removal by id is harmless when
        # nothing was stored, so withdraw on any failure.
        if stopped():
            await _withdraw_after_stop(
                plugin_id=plugin_id, name=name, role=role, registration_id=registration_id,
                why="register failed after plugin stop", cause=exc,
                body=exc.body if isinstance(exc, RemoteToolError) else None,
            )
        # The plugin runs. A definite failure (nothing sent, an error status,
        # ``rejected``) is raised as it is. A lost or unreadable answer leaves
        # the outcome unknown: main_server may hold the tool while nothing
        # here tracks it, and the notification / ``@llm_tool`` path sends no
        # cleanup, so model calls would get "not registered" until the plugin
        # stops. Reconcile it like a cancelled register.
        unknown = (
            exc.code == "timeout" if isinstance(exc, RemoteToolError)
            else not failed_before_send(httpx, exc)
        )
        if unknown:
            _, interrupted = await _finish_despite_cancellation(_reconcile_unknown_register(
                plugin_id=plugin_id, name=name, role=role, registration_id=registration_id,
                timeout_seconds=timeout_seconds, stopped=stopped,
                why="register answer lost",
            ))
            if interrupted:
                raise asyncio.CancelledError() from exc
        raise

    if plugin_stopped:
        await _withdraw_after_stop(
            plugin_id=plugin_id, name=name, role=role,
            registration_id=body.get("registration_id") or registration_id,
            why="register completed after plugin stop", cause=None, body=body,
        )
    logger.info(
        "Registered LLM tool '{}' for plugin '{}' (callback={})",
        name, plugin_id, callback_url,
    )
    return body


async def _withdraw_after_stop(
    *,
    plugin_id: str,
    name: str,
    role: Optional[str],
    registration_id: str,
    why: str,
    cause: Optional[BaseException],
    body: Optional[Dict[str, Any]],
) -> NoReturn:
    """Withdraw a register that ended after the plugin stopped and raise
    ``plugin_stopped`` (chained from ``cause``), or ``CancelledError`` if
    the caller was cancelled meanwhile. Always raises."""
    task, interrupted = await _finish_despite_cancellation(_remove_registration(
        plugin_id=plugin_id, name=name, role=role, registration_id=registration_id, why=why,
    ))
    if interrupted:
        raise asyncio.CancelledError() from cause
    withdrawn = task.result()
    outcome = (
        "the registration was withdrawn" if withdrawn
        # Also reached when nothing was stored (e.g. main_server unreachable),
        # so don't claim that something remains.
        else "its withdrawal could not be confirmed; if main_server stored it,"
        " it may remain until the plugin's next cleanup"
    )
    raise RemoteToolError(
        f"plugin '{plugin_id}' was stopped while '{name}' was being registered; {outcome}",
        code="plugin_stopped",
        # Once withdrawn, the roles of the register's answer no longer hold
        # the tool: don't report them as affected.
        body=None if withdrawn else body,
    ) from cause


async def _post_register(
    httpx: Any, client: Any, url: str, payload: Dict[str, Any], *, plugin_id: str, name: str,
) -> Dict[str, Any]:
    """POST a register and return main_server's accepting answer; raise for
    anything else (see ``register_remote_tool``)."""
    try:
        resp = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        # main_server may be down during plugin server boot. Don't crash
        # the plugin — just log loudly. The plugin can re-register later
        # by calling ctx.register_llm_tool again, e.g. on reload.
        logger.warning(
            "register_remote_tool HTTP error: plugin_id={}, name={}, err_type={}, err={}",
            plugin_id, name, type(exc).__name__, str(exc),
        )
        raise

    if resp.status_code >= 400:
        text = resp.text
        logger.warning(
            "register_remote_tool failed: plugin_id={}, name={}, status={}, body={}",
            plugin_id, name, resp.status_code, text[:500],
        )
        raise RemoteToolError(
            f"main_server /api/tools/register returned {resp.status_code}: {text[:500]}",
            code="main_server_error",
        )

    # An unreadable answer is not an acceptance: report it without tracking.
    try:
        body = resp.json()
    except ValueError as exc:
        raise RemoteToolError(
            f"main_server /api/tools/register returned an unreadable body: {exc}",
            code="timeout",  # answered, but unreadable: outcome unknown
        ) from exc
    if not isinstance(body, dict):
        raise RemoteToolError(
            "main_server /api/tools/register returned a non-object body",
            code="timeout",  # answered, but unreadable: outcome unknown
        )
    # ``main_server`` distinguishes HTTP-level failures (caught above as
    # status >= 400) from per-role logic failures — the latter come back
    # as 200 with ``{"ok": false, "failed_roles": [...]}`` when *no* role
    # accepted the registration. Treat that as a hard failure: skip
    # local tracking so a later ``has_plugin_tool`` doesn't lie, and
    # raise so the IPC handler logs and the caller can see what
    # happened.
    if body.get("ok") is False:
        affected = body.get("affected_roles") or []
        failed = body.get("failed_roles") or []
        logger.warning(
            "register_remote_tool rejected by main_server: plugin_id={}, name={}, "
            "affected_roles={}, failed_roles={}",
            plugin_id, name, affected, failed,
        )
        raise RemoteToolError(
            f"main_server rejected register for '{name}': affected={affected}, failed={failed}",
            code="rejected",
            body=body,
        )
    return body


async def _remove_registration(
    *, plugin_id: str, name: str, role: Optional[str], registration_id: str, why: str,
) -> bool:
    """Best-effort removal of one registration by id (caller holds the
    per-name lock). Returns whether main_server confirmed it is gone.

    Residual risk, accepted: when the register being withdrawn was
    cancelled mid-request, main_server may process that register after this
    removal (``removed: false``) and keep it. Both travel over loopback and
    the register was sent first, so this needs main_server to reorder two
    requests; the plugin-stop cleanup still removes it then.
    """
    try:
        await _unregister_remote_tool_locked(
            plugin_id=plugin_id, name=name, role=role,
            expected_registration_id=registration_id,
            request_timeout=_RECONCILE_TIMEOUT_SECONDS, reconcile_on_cancel=False,
            drop_tracking_only_of=registration_id,
        )
    except Exception as exc:
        logger.warning(
            "LLM tool registration could not be withdrawn ({}): plugin_id={}, name={}, "
            "err_type={}, err={}",
            why, plugin_id, name, type(exc).__name__, str(exc),
        )
        return False
    logger.info(
        "Withdrew LLM tool registration ({}): plugin_id={}, name={}", why, plugin_id, name,
    )
    return True


async def _reconcile_unknown_register(
    *,
    plugin_id: str,
    name: str,
    role: Optional[str],
    registration_id: str,
    timeout_seconds: float,
    stopped: Any,
    why: str,
) -> None:
    """Align host tracking with a register whose outcome is unknown (it was
    sent, then cancelled or its answer was lost). Caller holds the per-name
    lock."""
    if await _remove_registration(
        plugin_id=plugin_id, name=name, role=role, registration_id=registration_id, why=why,
    ):
        return
    # main_server may still hold the tool. The callback route answers model
    # calls for untracked tools with "not registered", so while the plugin
    # runs, track it: calls still reach the plugin, and a later unregister
    # or the plugin-stop cleanup removes both sides. This register was sent
    # after any earlier registration of the name and main_server replaces
    # on register, so it is the likelier one to be stored: an existing entry
    # is replaced with its values (the callback route reads this
    # ``timeout_seconds``).
    async with _lock:
        if not stopped():
            _plugin_tools[plugin_id][name] = {
                "timeout_seconds": float(timeout_seconds),
                "registration_id": registration_id,
            }


async def unregister_remote_tool(
    *,
    plugin_id: str,
    name: str,
    role: Optional[str] = None,
    expected_registration_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Unregister a single LLM tool from ``main_server``.

    HTTP first, local tracking second — and only if every role
    succeeded. ``main_server`` reports per-role failures as 200 with
    ``failed_roles=[...]``, so a partial success would leave the tool
    half-registered on some role; pruning local tracking in that case
    means a later ``clear_plugin_tools`` can't catch the stragglers and
    the model would still see them.

    The payload carries ``expected_source`` so ``main_server`` refuses to
    remove the name when it is owned by a different source (e.g. our own
    registration was rejected by the cross-source guard and the name
    actually belongs to another plugin) — a plugin can never unregister
    another plugin's tool by name collision.

    Runs under the per-name lock (see ``_tool_name_lock``): a late answer
    cannot drop the tracking of a register of the same name that started
    after this call.

    Cancelled after the request was sent, it sends the same unregister once
    more (still under the lock), aimed at a registration id (the expected
    one, else the tracked one; no retry without either), and updates
    tracking from that answer before the cancellation propagates. If the retry fails too, tracking is kept:
    a tracked tool main_server no longer holds is harmless, while an
    untracked one it still holds would fail every model call.
    """
    async with _tool_name_lock(plugin_id, name):
        return await _unregister_remote_tool_locked(
            plugin_id=plugin_id, name=name, role=role,
            expected_registration_id=expected_registration_id,
        )


async def _unregister_remote_tool_locked(
    *,
    plugin_id: str,
    name: str,
    role: Optional[str],
    expected_registration_id: Optional[str],
    request_timeout: Optional[float] = None,
    reconcile_on_cancel: bool = True,
    drop_tracking_only_of: Optional[str] = None,
) -> Dict[str, Any]:
    """``drop_tracking_only_of``: drop host tracking only when it records
    that registration id. Withdrawals of an interrupted register pass it:
    the tracked entry may belong to an earlier registration of the name
    (e.g. for another role) that main_server still holds."""
    payload = {"name": name, "role": role, "expected_source": _source_tag(plugin_id)}
    if expected_registration_id is not None:
        # Remove only that registration; a newer one of the same name stays.
        payload["expected_registration_id"] = expected_registration_id
    host_id = tool_host_instance_id()
    if host_id is not None:
        payload["expected_host_instance_id"] = host_id
    httpx = await ensure_httpx()
    client = _get_http_client()
    url = f"{_main_server_base_url()}/api/tools/unregister"
    try:
        if request_timeout is None:
            resp = await client.post(url, json=payload)
        else:
            resp = await asyncio.wait_for(
                client.post(
                    url, json=payload,
                    timeout=httpx.Timeout(request_timeout, connect=min(0.3, request_timeout)),
                ),
                request_timeout,
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "unregister_remote_tool HTTP error: plugin_id={}, name={}, err_type={}, err={}",
            plugin_id, name, type(exc).__name__, str(exc),
        )
        raise
    except asyncio.CancelledError:
        if reconcile_on_cancel:
            await _finish_despite_cancellation(_retry_cancelled_unregister(
                plugin_id=plugin_id, name=name, role=role,
                expected_registration_id=expected_registration_id,
            ))
        raise
    try:
        return await _apply_unregister_answer(plugin_id, name, resp, drop_tracking_only_of)
    except asyncio.CancelledError:
        # Only the wait for ``_lock`` can be interrupted here, after
        # main_server answered: reconcile the same way.
        if reconcile_on_cancel:
            await _finish_despite_cancellation(_retry_cancelled_unregister(
                plugin_id=plugin_id, name=name, role=role,
                expected_registration_id=expected_registration_id,
            ))
        raise


async def _retry_cancelled_unregister(
    *, plugin_id: str, name: str, role: Optional[str], expected_registration_id: Optional[str],
) -> None:
    # Either way the retry drops host tracking only of the registration it
    # targets: tracking may record another registration of the name.
    drop_tracking_only_of: Optional[str] = expected_registration_id
    if expected_registration_id is None:
        # The retry is bounded, so main_server may still be applying it after
        # the name lock is released. Unfiltered, it could then remove a later
        # registration of the name; aim it at the registration the host
        # tracks instead. Without one, nothing here routes to the tool, so
        # leave it to the request already sent.
        async with _lock:
            tracked = _plugin_tools.get(plugin_id, {}).get(name)
            expected_registration_id = (tracked or {}).get("registration_id") or None
        if expected_registration_id is None:
            logger.warning(
                "unregister_remote_tool cancelled after it was sent; no tracked registration id, "
                "not retried: plugin_id={}, name={}",
                plugin_id, name,
            )
            return
        drop_tracking_only_of = expected_registration_id
    try:
        await _unregister_remote_tool_locked(
            plugin_id=plugin_id, name=name, role=role,
            expected_registration_id=expected_registration_id,
            request_timeout=_RECONCILE_TIMEOUT_SECONDS, reconcile_on_cancel=False,
            drop_tracking_only_of=drop_tracking_only_of,
        )
    except Exception as exc:
        logger.warning(
            "unregister_remote_tool cancelled after it was sent and the retry failed; "
            "host tracking kept: plugin_id={}, name={}, err_type={}, err={}",
            plugin_id, name, type(exc).__name__, str(exc),
        )


async def _apply_unregister_answer(
    plugin_id: str, name: str, resp: Any, drop_tracking_only_of: Optional[str] = None,
) -> Dict[str, Any]:
    if resp.status_code >= 400:
        text = resp.text
        logger.warning(
            "unregister_remote_tool failed: plugin_id={}, name={}, status={}, body={}",
            plugin_id, name, resp.status_code, text[:500],
        )
        raise RemoteToolError(
            f"main_server /api/tools/unregister returned {resp.status_code}: {text[:500]}",
            code="main_server_error",
        )
    # An unreadable answer is not a removal: keep tracking and report it,
    # the same way register_remote_tool treats one.
    try:
        body = resp.json()
    except ValueError as exc:
        raise RemoteToolError(
            f"main_server /api/tools/unregister returned an unreadable body: {exc}",
            code="timeout",  # answered, but unreadable: outcome unknown
        ) from exc
    if not isinstance(body, dict):
        raise RemoteToolError(
            "main_server /api/tools/unregister returned a non-object body",
            code="timeout",  # answered, but unreadable: outcome unknown
        )
    refused_roles = body.get("refused_roles")
    if refused_roles:
        # Another source owns the name: our registration never took effect on main_server (cross-source
        # guard rejection, cache races, etc.). Discard local tracking without touching others' tools.
        logger.warning(
            "unregister_remote_tool refused foreign-owned name: plugin_id={}, name={}, refused_roles={}",
            plugin_id, name, refused_roles,
        )
    failed_roles = body.get("failed_roles")
    if failed_roles:
        logger.warning(
            "unregister_remote_tool partial failure — keeping local tracking: "
            "plugin_id={}, name={}, failed_roles={}",
            plugin_id, name, failed_roles,
        )
        # Local tracking stays so ``clear_plugin_tools`` on shutdown
        # still attempts to wipe the orphaned roles. Surface as an
        # error so the IPC handler logs visibly.
        raise RemoteToolError(
            f"main_server reported failed_roles={failed_roles} for unregister of '{name}'",
            code="partial_failure",
            body=body,
        )
    async with _lock:
        owned = _plugin_tools.get(plugin_id)
        tracked = owned.get(name) if owned is not None else None
        other_registration = any(
            isinstance(r, dict) and r.get("reason") == "different registration"
            for r in (refused_roles or [])
        )
        if (
            tracked is not None and not other_registration
            and (drop_tracking_only_of is None or tracked.get("registration_id") == drop_tracking_only_of)
        ):
            # A refusal for "different registration" means main_server still
            # holds another registration of this plugin under the name (e.g.
            # a timed-out register that landed late): model calls still come
            # here, so keep tracking until that one is removed too.
            owned.pop(name, None)
            if not owned:
                _plugin_tools.pop(plugin_id, None)
    return body


# Remote-tool cleanup timeout on plugin stop: much shorter than the default 2.0s connect timeout,
# because this is best-effort cleanup under the cross-process lock; every extra second delays all plugin operations.
# The default timeout is constructed with the backend on first cleanup.


async def clear_plugin_tools(
    plugin_id: str, *, role: Optional[str] = None, timeout: float | None = None
) -> Dict[str, Any]:
    """Remove every LLM tool a plugin has registered on ``main_server``.

    Called from the plugin shutdown path so a stopped plugin doesn't
    leave dangling tools the model can still try to call. Best-effort:
    swallows HTTP errors (the plugin is already going away; a noisy
    failure here would mask the real shutdown reason).

    Also marks the plugin as stopped for registers still in flight (see
    ``_stop_generations``): they withdraw what main_server stored instead
    of tracking it, since this cleanup may already be past them.
    """
    # Before any await, so a register answered from here on sees the stop.
    _stop_generations[plugin_id] += 1
    httpx = await ensure_httpx()
    async with _lock:
        owned = list(_plugin_tools.pop(plugin_id, {}).keys())

    payload = {"source": _source_tag(plugin_id), "role": role}
    client = _get_http_client()
    url = f"{_main_server_base_url()}/api/tools/clear"
    # This await holds stop_plugin's cross-process lock. If main_server is not listening, local
    # loopback refusal consumes the full default 2.0s connect timeout; reloading eight plugins creates
    # over ten seconds of pure dead time in the lock chain.
    #
    # Skipping when no local record exists is wrong: after process restart _plugin_tools is empty,
    # but main_server may still hold tools registered by this plugin. Skipping leaves ghost tools
    # callable by the model. Local records are not authoritative for remote state, so send cleanup
    # anyway, with a shorter best-effort timeout.
    try:
        # Honor caller budgets: plugin stop is on the critical path and still holds the operation lock;
        # a separate timeout would extend shutdown beyond the overall budget (Codex).
        #
        # httpx.Timeout limits individual connect/read/write/pool phases, not total duration: a slowly
        # trickling chunked response can avoid every phase timeout while exceeding the overall budget
        # (CodeRabbit verified this with a real chunked server). Retain phase limits and wrap
        # the call in asyncio.wait_for for a total-duration limit.
        request = client.post(
            url,
            json=payload,
            timeout=(
                httpx.Timeout(2.0, connect=0.3)
                if timeout is None
                else httpx.Timeout(timeout, connect=min(0.3, timeout))
            ),
        )
        resp = await (request if timeout is None else asyncio.wait_for(request, timeout))
    except asyncio.TimeoutError:
        logger.debug(
            "clear_plugin_tools exceeded its budget (best-effort): plugin_id={}, timeout={}",
            plugin_id, timeout,
        )
        return {"ok": False, "removed": 0, "error": "timeout", "owned_count": len(owned)}
    except httpx.HTTPError as exc:
        logger.debug(
            "clear_plugin_tools HTTP error (best-effort): plugin_id={}, err={}",
            plugin_id, str(exc),
        )
        return {"ok": False, "removed": 0, "error": str(exc), "owned_count": len(owned)}

    if resp.status_code >= 400:
        logger.debug(
            "clear_plugin_tools non-200: plugin_id={}, status={}, body={}",
            plugin_id, resp.status_code, resp.text[:200],
        )
        return {
            "ok": False,
            "removed": 0,
            "status_code": resp.status_code,
            "owned_count": len(owned),
        }
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(body, dict):
        body.setdefault("owned_count", len(owned))
        return body
    return {"ok": True, "removed": 0, "owned_count": len(owned)}


# ---------------------------------------------------------------------------
# Local introspection (used by tests + diagnostics)
# ---------------------------------------------------------------------------


def get_plugin_tool_names(plugin_id: str) -> List[str]:
    """Return the tool names this process believes ``plugin_id`` has
    registered. Note this is a snapshot of *our* tracking, not of
    ``main_server``'s actual registry — the two can diverge if
    ``main_server`` was restarted without restarting the plugin server.
    """
    return sorted(_plugin_tools.get(plugin_id, {}).keys())


def has_plugin_tool(plugin_id: str, name: str) -> bool:
    return name in _plugin_tools.get(plugin_id, {})


def get_plugin_tool_timeout(plugin_id: str, name: str) -> Optional[float]:
    """Return the per-tool ``timeout_seconds`` recorded at registration
    time, or ``None`` if the tool isn't tracked. The callback route uses
    this to hand the right timeout to ``host.trigger`` instead of
    hard-coding a default — without it, a 90-second tool would be cut
    off at 30s on the plugin side while ``main_server`` was still
    waiting up to 90s for the HTTP response.
    """
    entry = _plugin_tools.get(plugin_id, {}).get(name)
    if entry is None:
        return None
    timeout = entry.get("timeout_seconds")
    return float(timeout) if isinstance(timeout, (int, float)) else None

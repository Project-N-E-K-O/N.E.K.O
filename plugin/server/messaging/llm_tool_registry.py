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
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

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

# Lazy-initialised httpx client. We share one client across registrations
# because the plugin server may register dozens of tools at startup —
# creating one client per call would balloon TCP connections.
_HTTP_CLIENT: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        httpx = load_httpx()
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=2.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
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
) -> Dict[str, Any]:
    """Register a plugin-owned LLM tool with ``main_server``.

    Idempotent at the registry level: if the same ``(plugin_id, name)``
    is registered twice, the second call still POSTs to ``main_server``
    (which uses ``replace=True`` semantics in
    ``register_tool_and_sync``) and overwrites the first.

    Returns the parsed JSON body from ``main_server``. Raises any
    httpx error so the caller can surface it back to the plugin via
    IPC.
    """
    callback_url = build_callback_url(plugin_id, name)
    httpx = await ensure_httpx()
    payload = {
        "name": name,
        "description": description,
        "parameters": parameters,
        "callback_url": callback_url,
        "role": role,
        "source": _source_tag(plugin_id),
        "timeout_seconds": float(timeout_seconds),
        **({"reply_tail": True} if reply_tail else {}),
    }

    client = _get_http_client()
    url = f"{_main_server_base_url()}/api/tools/register"
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
        raise RuntimeError(
            f"main_server /api/tools/register returned {resp.status_code}: {text[:500]}"
        )

    body = resp.json()
    # ``main_server`` distinguishes HTTP-level failures (caught above as
    # status >= 400) from per-role logic failures — the latter come back
    # as 200 with ``{"ok": false, "failed_roles": [...]}`` when *no* role
    # accepted the registration. Treat that as a hard failure: skip
    # local tracking so a later ``has_plugin_tool`` doesn't lie, and
    # raise so the IPC handler logs and the caller can see what
    # happened.
    if isinstance(body, dict) and body.get("ok") is False:
        affected = body.get("affected_roles") or []
        failed = body.get("failed_roles") or []
        logger.warning(
            "register_remote_tool rejected by main_server: plugin_id={}, name={}, "
            "affected_roles={}, failed_roles={}",
            plugin_id, name, affected, failed,
        )
        raise RuntimeError(
            f"main_server rejected register for '{name}': affected={affected}, failed={failed}"
        )
    async with _lock:
        _plugin_tools[plugin_id][name] = {"timeout_seconds": float(timeout_seconds)}
    logger.info(
        "Registered LLM tool '{}' for plugin '{}' (callback={})",
        name, plugin_id, callback_url,
    )
    return body


async def unregister_remote_tool(
    *,
    plugin_id: str,
    name: str,
    role: Optional[str] = None,
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
    """
    payload = {"name": name, "role": role, "expected_source": _source_tag(plugin_id)}
    httpx = await ensure_httpx()
    client = _get_http_client()
    url = f"{_main_server_base_url()}/api/tools/unregister"
    try:
        resp = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning(
            "unregister_remote_tool HTTP error: plugin_id={}, name={}, err_type={}, err={}",
            plugin_id, name, type(exc).__name__, str(exc),
        )
        raise

    if resp.status_code >= 400:
        text = resp.text
        logger.warning(
            "unregister_remote_tool failed: plugin_id={}, name={}, status={}, body={}",
            plugin_id, name, resp.status_code, text[:500],
        )
        raise RuntimeError(
            f"main_server /api/tools/unregister returned {resp.status_code}: {text[:500]}"
        )
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    refused_roles = body.get("refused_roles") if isinstance(body, dict) else None
    if refused_roles:
        # Another source owns the name: our registration never took effect on main_server (cross-source
        # guard rejection, cache races, etc.). Discard local tracking without touching others' tools.
        logger.warning(
            "unregister_remote_tool refused foreign-owned name: plugin_id={}, name={}, refused_roles={}",
            plugin_id, name, refused_roles,
        )
    failed_roles = body.get("failed_roles") if isinstance(body, dict) else None
    if failed_roles:
        logger.warning(
            "unregister_remote_tool partial failure — keeping local tracking: "
            "plugin_id={}, name={}, failed_roles={}",
            plugin_id, name, failed_roles,
        )
        # Local tracking stays so ``clear_plugin_tools`` on shutdown
        # still attempts to wipe the orphaned roles. Surface as an
        # error so the IPC handler logs visibly.
        raise RuntimeError(
            f"main_server reported failed_roles={failed_roles} for unregister of '{name}'"
        )
    async with _lock:
        owned = _plugin_tools.get(plugin_id)
        if owned is not None:
            owned.pop(name, None)
            if not owned:
                _plugin_tools.pop(plugin_id, None)
    return body if isinstance(body, dict) else {"ok": True}


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
    """
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

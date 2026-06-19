"""HTTP route that receives LLM tool dispatches from ``main_server``
and forwards them into the right plugin process via IPC.

Lifecycle::

    LLM picks a tool ──► main_server (port 48911) tool_router
                          │
                          │ POST callback_url
                          ▼
    user_plugin_server (port 48916) /api/llm-tools/callback/{pid}/{tool}
                          │
                          │ host.trigger("__llm_tool__{tool}", args)
                          ▼
    plugin process — runs the @llm_tool-decorated method

The plugin's handler is stored as a *dynamic entry* on the plugin side
(see ``plugin/sdk/plugin/llm_tool.py``); we therefore reuse the
already-mature ``comm_manager.trigger`` IPC plumbing instead of
inventing a parallel command channel. The entry id namespace is
``__llm_tool__{name}`` to avoid colliding with regular plugin entries.

Security
--------
``main_server``'s tool_router refuses to register a non-loopback
``callback_url`` (see ``_validate_local_callback_url`` there), and
``user_plugin_server`` itself only binds 127.0.0.1. Together that means
any request reaching this route originated from ``main_server`` running
on the same machine. We do not double-check loopback origin here; if
that boundary were ever loosened we would need to.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.messaging.llm_tool_registry import (
    get_plugin_tool_timeout,
    has_plugin_tool,
)

logger = get_logger("server.routes.llm_tools")

router = APIRouter(prefix="/api/llm-tools", tags=["llm-tools"])

# Fallback when the per-tool timeout isn't tracked (shouldn't happen
# normally because ``has_plugin_tool`` guards entry, but kept as a safety
# net so a registry desync doesn't 5xx the dispatcher). Mirrors
# ``ToolRegisterRequest.timeout_seconds`` default in
# ``main_routers/tool_router.py``.
_DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


def _entry_id_for_tool(tool_name: str) -> str:
    """Namespace LLM tool entries so they don't collide with regular
    plugin entries that happen to share a name."""
    return f"__llm_tool__{tool_name}"


@router.post("/callback/{plugin_id}/{tool_name}")
async def llm_tool_callback(
    plugin_id: str,
    tool_name: str,
    body: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    """Dispatch an LLM tool call into the plugin process.

    Request body shape (sent by ``main_server``)::

        {"name": str, "arguments": dict, "call_id": str, "raw_arguments": str}

    Response body shape::

        {"output": <any JSON>, "is_error": bool, "error": <optional str>}

    ``main_server``'s ``_remote_dispatch`` translates a non-2xx status or
    a JSON ``is_error: true`` into a ``ToolResult.is_error`` so the model
    sees the error inline. We follow that contract precisely: errors
    here always return HTTP 200 with ``is_error: true``, never 5xx,
    because a 5xx makes the model see a dispatcher failure rather than a
    tool-level failure.
    """
    arguments = body.get("arguments")
    if not isinstance(arguments, dict):
        # Defensive: the LLM might emit an empty arguments object as a
        # JSON array or string; coerce or reject explicitly so the
        # plugin never receives a non-dict.
        arguments = {}
    call_id = body.get("call_id") or ""

    # First sanity check: do we even know about this tool? Without this,
    # a stale main_server registration after a plugin restart would
    # quietly hang on the IPC trigger until the timeout. Returning 404
    # cleanly is much friendlier.
    if not has_plugin_tool(plugin_id, tool_name):
        logger.warning(
            "llm_tool_callback: unknown tool plugin_id={}, tool={} (call_id={})",
            plugin_id, tool_name, call_id,
        )
        return {
            "output": {"error": f"tool '{tool_name}' is not registered for plugin '{plugin_id}'"},
            "is_error": True,
            "error": "TOOL_NOT_REGISTERED",
        }

    # Look up the live plugin host. If the plugin crashed or was stopped
    # without clearing its tools (e.g. process kill), main_server still
    # has the registration but we can't dispatch — surface that as a
    # tool-level error.
    host_obj = None
    with state.acquire_plugin_hosts_read_lock():
        host_obj = state.plugin_hosts.get(plugin_id)
    if host_obj is None:
        logger.warning(
            "llm_tool_callback: plugin not running plugin_id={}, tool={}",
            plugin_id, tool_name,
        )
        return {
            "output": {"error": f"plugin '{plugin_id}' is not running"},
            "is_error": True,
            "error": "PLUGIN_NOT_RUNNING",
        }

    trigger = getattr(host_obj, "trigger", None)
    if not callable(trigger):
        # Should not happen in practice; the host contract guarantees
        # ``trigger``. If it does, treat it as a structural bug, not a
        # tool error — bubble up as 500 so it's loud in logs.
        raise HTTPException(status_code=500, detail="plugin host has no trigger() method")

    entry_id = _entry_id_for_tool(tool_name)
    # Use the per-tool timeout that was recorded at registration time
    # so a long-running tool isn't cut off at 30s on the plugin side
    # while ``main_server`` is still waiting for the HTTP response.
    # ``main_server`` itself uses the same value as its httpx timeout
    # (see ``_remote_dispatch``), so the two sides line up — small
    # buffer not subtracted because a plugin-side timeout already
    # produces a clean error JSON before main_server's HTTP timeout
    # fires (the await on ``trigger`` raises ``TimeoutError`` which we
    # convert into ``is_error: True`` below).
    registered_timeout = get_plugin_tool_timeout(plugin_id, tool_name)
    timeout_seconds = (
        float(registered_timeout)
        if registered_timeout is not None and registered_timeout > 0
        else _DEFAULT_TOOL_TIMEOUT_SECONDS
    )

    # The plugin handler is invoked with ``arguments`` as kwargs; the
    # plugin SDK's adapter wraps the registered handler so it receives
    # ``**arguments`` directly (see ``register_llm_tool`` in
    # ``plugin/sdk/plugin/llm_tool.py``).
    try:
        result = await trigger(entry_id, arguments, timeout_seconds)
    except TimeoutError as exc:
        # comm_manager.trigger raises plain TimeoutError on no-response
        # within `timeout`; surface as a tool error so the LLM can retry
        # rather than the dispatcher 5xxing.
        logger.warning(
            "llm_tool_callback timeout: plugin_id={}, tool={}, err={}",
            plugin_id, tool_name, str(exc),
        )
        return {
            "output": {"error": f"tool '{tool_name}' timed out after {timeout_seconds}s"},
            "is_error": True,
            "error": "TOOL_TIMEOUT",
        }
    except Exception as exc:
        logger.exception(
            "llm_tool_callback exception: plugin_id={}, tool={}, err_type={}",
            plugin_id, tool_name, type(exc).__name__,
        )
        return {
            "output": {"error": f"{type(exc).__name__}: {exc}"},
            "is_error": True,
            "error": type(exc).__name__,
        }

    # The plugin's handler can return either:
    #  - A plain value (str/dict/list/...) → wrap in {"output": value}.
    #  - A dict already shaped {"output": ..., "is_error": ...} →
    #    pass through verbatim. This is the path used when a handler
    #    wraps its own error semantics, e.g. via the SDK's Result type.
    if isinstance(result, dict) and "is_error" in result and "output" in result:
        out = {"output": result["output"], "is_error": bool(result["is_error"])}
        if result.get("error"):
            out["error"] = str(result["error"])
        return out
    return {"output": result, "is_error": False}

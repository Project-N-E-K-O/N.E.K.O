"""Request-response handlers for plugin LLM tool registration.

The fire-and-forget ``LLM_TOOL_REGISTER`` / ``LLM_TOOL_UNREGISTER``
notifications (``plugin/core/communication.py``) only log what
``main_server`` answered. These two handlers run the same host-side calls
(``register_remote_tool`` / ``unregister_remote_tool``) but return the
outcome to the plugin, so ``register_llm_tool_async`` /
``unregister_llm_tool_async`` can report it and keep the SDK's local state
in step with the host.

The plugin id always comes from the authenticated connection
(``ctx.from_plugin``), never from the payload; unregister therefore always
carries ``expected_source=plugin:{from_plugin}``.

Result shapes and error codes are defined once, next to the SDK API, in
``plugin/sdk/plugin/llm_tool.py`` (``LlmToolRegisterResult`` /
``LlmToolUnregisterResult``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plugin.core.rpc import RpcContext
from plugin.server.messaging.llm_tool_registry import (
    RemoteToolError,
    parse_register_request,
    parse_unregister_request,
    register_remote_tool,
    unregister_remote_tool,
)
from plugin.utils.http_imports import ensure_httpx


# ``plugin.sdk.plugin.llm_tool`` is imported on first use, not at module
# import: importing it runs ``plugin/sdk/plugin/__init__.py``, which loads the
# whole plugin SDK, and this module is imported whenever the plugin server
# starts (``request_router`` builds its handlers then). The server keeps the
# plugin execution capabilities unloaded until a plugin actually needs them.
def _register_result(
    body: Mapping[str, Any], *, ok: bool, error: str | None = None, message: str | None = None,
) -> object:
    from plugin.sdk.plugin.llm_tool import build_register_result

    return build_register_result(body, ok=ok, error=error, message=message)


def _unregister_result(
    body: Mapping[str, Any], *, ok: bool, error: str | None = None, message: str | None = None,
) -> object:
    from plugin.sdk.plugin.llm_tool import build_unregister_result

    return build_unregister_result(body, ok=ok, error=error, message=message)


def _transport_failure(httpx, exc: Exception) -> tuple[str, str]:
    """Map an httpx error from the main_server call to an ``error`` code.

    Only errors that certainly happen before the request is sent (connect
    failure or timeout, no pooled connection, proxy, unsupported scheme,
    invalid request on our side) are ``main_server_unavailable``. Anything
    else (read/write timeout or failure, connection dropped mid-exchange,
    undecodable answer, ...) means main_server may have applied the change,
    so it is reported as ``timeout``: the outcome is unknown.
    """
    message = f"{type(exc).__name__}: {exc}"
    before_send = (
        httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout,
        httpx.ProxyError, httpx.UnsupportedProtocol, httpx.LocalProtocolError,
    )
    if isinstance(exc, before_send):
        return "main_server_unavailable", message
    return "timeout", message


async def handle_llm_tool_register(ctx: RpcContext, request: dict[str, object]) -> object:
    fields = parse_register_request(request)
    if fields is None:
        return _register_result(
            {}, ok=False, error="invalid_request", message="missing or invalid tool name or registration id",
        )
    httpx = await ensure_httpx()
    try:
        body = await register_remote_tool(plugin_id=ctx.from_plugin, **fields)
    except RemoteToolError as exc:
        return _register_result(exc.body, ok=False, error=exc.code, message=str(exc))
    except httpx.HTTPError as exc:
        error, message = _transport_failure(httpx, exc)
        return _register_result({}, ok=False, error=error, message=message)
    return _register_result(body if isinstance(body, dict) else {}, ok=True)


async def handle_llm_tool_unregister(ctx: RpcContext, request: dict[str, object]) -> object:
    fields = parse_unregister_request(request)
    if fields is None:
        return _unregister_result(
            {}, ok=False, error="invalid_request", message="missing or invalid tool name or registration id",
        )
    httpx = await ensure_httpx()
    try:
        body = await unregister_remote_tool(plugin_id=ctx.from_plugin, **fields)
    except RemoteToolError as exc:
        return _unregister_result(exc.body, ok=False, error=exc.code, message=str(exc))
    except httpx.HTTPError as exc:
        error, message = _transport_failure(httpx, exc)
        return _unregister_result({}, ok=False, error=error, message=message)
    return _unregister_result(body if isinstance(body, dict) else {}, ok=True)

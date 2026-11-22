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

from plugin.core.rpc import RpcContext
from plugin.sdk.plugin.llm_tool import (
    build_register_result as _register_result,
    build_unregister_result as _unregister_result,
)
from plugin.server.messaging.llm_tool_registry import (
    RemoteToolError,
    parse_register_request,
    parse_unregister_request,
    register_remote_tool,
    unregister_remote_tool,
)
from plugin.utils.http_imports import ensure_httpx


async def handle_llm_tool_register(ctx: RpcContext, request: dict[str, object]) -> object:
    fields = parse_register_request(request)
    if fields is None:
        return _register_result({}, ok=False, error="invalid_request", message="missing tool name")
    httpx = await ensure_httpx()
    try:
        body = await register_remote_tool(plugin_id=ctx.from_plugin, **fields)
    except RemoteToolError as exc:
        return _register_result(exc.body, ok=False, error=exc.code, message=str(exc))
    except httpx.HTTPError as exc:
        return _register_result(
            {}, ok=False, error="main_server_unavailable", message=f"{type(exc).__name__}: {exc}",
        )
    except ValueError as exc:  # main_server answered, but not with JSON
        return _register_result(
            {}, ok=False, error="main_server_error", message=f"{type(exc).__name__}: {exc}",
        )
    return _register_result(body if isinstance(body, dict) else {}, ok=True)


async def handle_llm_tool_unregister(ctx: RpcContext, request: dict[str, object]) -> object:
    fields = parse_unregister_request(request)
    if fields is None:
        return _unregister_result({}, ok=False, error="invalid_request", message="missing tool name")
    httpx = await ensure_httpx()
    try:
        body = await unregister_remote_tool(plugin_id=ctx.from_plugin, **fields)
    except RemoteToolError as exc:
        return _unregister_result(exc.body, ok=False, error=exc.code, message=str(exc))
    except httpx.HTTPError as exc:
        return _unregister_result(
            {}, ok=False, error="main_server_unavailable", message=f"{type(exc).__name__}: {exc}",
        )
    except ValueError as exc:  # main_server answered, but not with JSON
        return _unregister_result(
            {}, ok=False, error="main_server_error", message=f"{type(exc).__name__}: {exc}",
        )
    return _unregister_result(body if isinstance(body, dict) else {}, ok=True)

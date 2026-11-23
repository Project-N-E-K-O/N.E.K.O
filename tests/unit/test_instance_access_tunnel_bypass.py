"""Security regression tests for R1111-01 remote/proxy loopback tunnel bypass and internal service proof."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request as FastAPIRequest, WebSocket
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest
from starlette.websockets import WebSocketDisconnect

import config
from config import MAIN_SERVER_PORT, MEMORY_SERVER_PORT, TOOL_SERVER_PORT, USER_PLUGIN_SERVER_PORT
from utils.http.internal_client import (
    InternalServiceSigningError,
    _httpx_signing_path,
    _should_attach_internal_service_proof,
    aclose_internal_http_client_current_loop,
    attach_internal_service_proof,
    attach_internal_service_proof_sync,
    get_internal_http_client,
    internal_service_event_hooks,
    merge_internal_service_event_hooks,
)
from utils.instance_access import (
    INTERNAL_SERVICE_HEADER,
    InstanceAccessMiddleware,
    _local_native,
    _request_signing_path,
    internal_service_proof,
)

KEY = "test-only-r1111-key-" + "k" * 40


def _build_guarded_app() -> FastAPI:
    app = FastAPI()

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        return {"status": "ok"}

    @app.websocket("/health")
    async def health_ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ws-health")
        await socket.close()

    @app.get("/private")
    async def private_get():
        return {"ok": True}

    @app.post("/private")
    async def private_post():
        return {"ok": True}

    @app.websocket("/socket")
    async def ws_endpoint(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"ok": True})
        await socket.close()

    app.add_middleware(InstanceAccessMiddleware)
    return app


_DEPLOYMENT_MODE_ENV_VARS = (
    "NEKO_BEHIND_PROXY",
    "NEKO_ACTIVITY_TRACKER_REMOTE",
    "ACTIVITY_TRACKER_REMOTE",
)


def _clear_deployment_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_key in _DEPLOYMENT_MODE_ENV_VARS:
        monkeypatch.delenv(env_key, raising=False)


@pytest.mark.parametrize(
    ("env_name", "env_val"),
    [
        ("NEKO_BEHIND_PROXY", "true"),
        ("NEKO_ACTIVITY_TRACKER_REMOTE", "true"),
    ],
)
def test_remote_or_proxy_mode_closes_headerless_loopback_tunnel_bypass(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_val: str,
):
    """R1111-01: headerless loopback requests must be rejected on private HTTP and WS routes in proxy/remote mode."""
    _clear_deployment_mode_env(monkeypatch)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv(env_name, env_val)

    client = TestClient(_build_guarded_app(), base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))

    # Headerless HTTP routes (GET and POST) must require credentials (401).
    assert client.get("/private").status_code == 401
    assert client.post("/private").status_code == 401

    # Headerless WebSocket on loopback (including /health) must also be rejected with close code 4401.
    for ws_path in ("/socket", "/health"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"ws://127.0.0.1:48911{ws_path}"):
                pass
        assert exc_info.value.code == 4401

    # Local unauthenticated GET/HEAD /health remains allowed without forwarding/browser headers.
    assert client.get("/health").status_code == 200
    assert client.head("/health").status_code == 200

    # Forwarded /health must still be rejected.
    assert client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401


@pytest.mark.parametrize(
    ("env_name", "env_val"),
    [
        ("NEKO_BEHIND_PROXY", "true"),
        ("NEKO_ACTIVITY_TRACKER_REMOTE", "true"),
    ],
)
async def test_remote_or_proxy_mode_closes_raw_asgi_websocket_health_without_upgrade_header(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_val: str,
):
    """R1111-01: raw ASGI WebSocket scopes on /health without Upgrade header must close with 4401."""
    _clear_deployment_mode_env(monkeypatch)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv(env_name, env_val)

    called = False

    async def inner_app(_scope, _receive, send):
        nonlocal called
        called = True
        await send({"type": "websocket.accept"})

    middleware = InstanceAccessMiddleware(inner_app)
    sent = []
    scope = {
        "type": "websocket",
        "scheme": "ws",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "server": ("127.0.0.1", 48911),
        "client": ("127.0.0.1", 51234),
        "headers": [(b"host", b"127.0.0.1:48911")],
    }

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    assert called is False
    assert sent == [{"type": "websocket.close", "code": 4401}]


async def test_internal_http_client_attaches_signed_proof_in_remote_mode_and_validates_in_middleware(
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: internal HTTP client injects X-Neko-Internal-Service in proxy/remote mode and passes middleware."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    internal_client = get_internal_http_client()
    try:
        # Exercise the registered request event hook directly against an ASGI app.
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        hooked_client = httpx.AsyncClient(
            transport=transport,
            event_hooks=internal_client.event_hooks,
        )
        async with hooked_client:
            resp = await hooked_client.post(f"http://127.0.0.1:{MAIN_SERVER_PORT}/private")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
    finally:
        await aclose_internal_http_client_current_loop()


def test_internal_service_proof_rejects_forged_expired_wrong_path_and_forwarded_requests(
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: X-Neko-Internal-Service proof must reject tampering, expiry, wrong method/path, proxy headers, and remote peers."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    base = f"http://127.0.0.1:{MAIN_SERVER_PORT}"
    local_client = TestClient(app, base_url=base, client=("127.0.0.1", 50000))
    remote_client = TestClient(app, base_url=base, client=("203.0.113.9", 50000))

    valid_proof = internal_service_proof(KEY, "GET", "/private", port=MAIN_SERVER_PORT)
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 200

    # Wrong method, wrong path, or wrong target port fails.
    assert local_client.post("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 401
    wrong_path_proof = internal_service_proof(KEY, "GET", "/other", port=MAIN_SERVER_PORT)
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: wrong_path_proof}).status_code == 401
    wrong_port_proof = internal_service_proof(KEY, "GET", "/private", port=USER_PLUGIN_SERVER_PORT)
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: wrong_port_proof}).status_code == 401
    portless_proof = internal_service_proof(KEY, "GET", "/private")
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: portless_proof}).status_code == 401

    # Expired proof fails.
    expired_proof = internal_service_proof(KEY, "GET", "/private", port=MAIN_SERVER_PORT, expires=int(time.time()) - 10)
    assert local_client.get("/private", headers={INTERNAL_SERVICE_HEADER: expired_proof}).status_code == 401

    # Forwarding headers or non-loopback peer fail even with a valid proof.
    assert local_client.get(
        "/private",
        headers={INTERNAL_SERVICE_HEADER: valid_proof, "X-Forwarded-For": "127.0.0.1"},
    ).status_code == 401
    assert remote_client.get("/private", headers={INTERNAL_SERVICE_HEADER: valid_proof}).status_code == 401


def test_desktop_mode_keeps_zero_step_loopback_without_touching_instance_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """R1111-01: desktop mode (no proxy/remote env) preserves zero-step loopback and creates no instance_access.key."""
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("NEKO_INSTANCE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))

    client = TestClient(_build_guarded_app(), base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))
    assert client.get("/private").status_code == 200
    assert client.post("/private").status_code == 200
    with client.websocket_connect("ws://127.0.0.1:48911/socket") as ws:
        assert ws.receive_json() == {"ok": True}
    assert not (tmp_path / "instance_access.key").exists()
    assert callable(_local_native)


async def test_raw_path_signing_parity_across_client_and_middleware(
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: _httpx_signing_path and _request_signing_path agree on percent-encoded raw_path without query."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    url = f"http://127.0.0.1:{MAIN_SERVER_PORT}/release/%E5%96%B5%3F%23?a=1&b=2"
    raw_req = httpx.Request("GET", url)
    assert _httpx_signing_path(raw_req) == "/release/%E5%96%B5%3F%23"

    starlette_req = StarletteRequest(
        {
            "type": "http",
            "method": "GET",
            "path": "/release/喵?#",
            "raw_path": b"/release/%E5%96%B5%3F%23",
            "query_string": b"a=1&b=2",
            "headers": [],
            "scheme": "http",
            "server": ("127.0.0.1", MAIN_SERVER_PORT),
        }
    )
    assert _request_signing_path(starlette_req) == "/release/%E5%96%B5%3F%23"

    app = FastAPI()
    seen: list[tuple[str, str]] = []

    @app.get("/release/{item}")
    async def release_item(request: FastAPIRequest, item: str):
        seen.append((item, _request_signing_path(request)))
        return {"ok": True, "item": item}

    app.add_middleware(InstanceAccessMiddleware)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with httpx.AsyncClient(transport=transport) as unsigned:
        assert (await unsigned.get(url)).status_code == 401

    wrong_decoded_proof = internal_service_proof(KEY, "GET", "/release/喵", port=MAIN_SERVER_PORT)
    async with httpx.AsyncClient(transport=transport) as bad_signed:
        assert (
            await bad_signed.get(url, headers={INTERNAL_SERVICE_HEADER: wrong_decoded_proof})
        ).status_code == 401

    async with httpx.AsyncClient(
        transport=transport,
        event_hooks=internal_service_event_hooks(),
    ) as signed:
        resp = await signed.get(url)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "item": "喵?#"}
    assert seen == [("喵?#", "/release/%E5%96%B5%3F%23")]

    custom_called: list[str] = []

    def _extra_sync_hook(req: httpx.Request) -> None:
        custom_called.append(req.url.path)

    merged_sync = merge_internal_service_event_hooks({"request": [_extra_sync_hook]}, sync=True)
    assert attach_internal_service_proof_sync in merged_sync["request"]
    assert _extra_sync_hook in merged_sync["request"]

    sync_headers: list[str] = []
    mock_transport = httpx.MockTransport(
        lambda req: (sync_headers.append(req.headers.get(INTERNAL_SERVICE_HEADER, "")), httpx.Response(200, json={"ok": True}))[1]
    )
    with httpx.Client(transport=mock_transport, event_hooks=merged_sync) as sync_client:
        sync_resp = sync_client.get(url)
        assert sync_resp.status_code == 200
    assert custom_called == ["/release/喵?#"]
    assert sync_headers and sync_headers[0]
    assert attach_internal_service_proof in internal_service_event_hooks()["request"]


async def test_migrated_internal_loopback_callers_succeed_under_proxy_mode(
    clean_user_data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    """R1111-01: migrated internal loopback callers attach signed proof and pass InstanceAccessMiddleware."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = FastAPI()
    recorded: list[tuple[str, str, bool]] = []

    @app.post("/notify_config_changed")
    async def notify_config_changed(request: FastAPIRequest):
        recorded.append(("POST", request.url.path, bool(request.headers.get(INTERNAL_SERVICE_HEADER))))
        return {"status": "ok"}

    @app.post("/reload")
    async def reload_memory(request: FastAPIRequest):
        recorded.append(("POST", request.url.path, bool(request.headers.get(INTERNAL_SERVICE_HEADER))))
        return {"status": "success"}

    @app.get("/plugins")
    async def list_plugins(request: FastAPIRequest):
        recorded.append(("GET", request.url.path, bool(request.headers.get(INTERNAL_SERVICE_HEADER))))
        return {"plugins": []}

    @app.post("/api/tools/register")
    async def register_tool_route(request: FastAPIRequest):
        recorded.append(("POST", request.url.path, bool(request.headers.get(INTERNAL_SERVICE_HEADER))))
        return {"ok": True, "affected_roles": ["Lanlan"], "failed_roles": []}

    @app.post("/api/llm-tools/callback/p/t")
    async def plugin_tool_callback(request: FastAPIRequest):
        recorded.append(("POST", request.url.path, bool(request.headers.get(INTERNAL_SERVICE_HEADER))))
        return {"output": {"ran": True}, "is_error": False}

    app.add_middleware(InstanceAccessMiddleware)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

    # Sanity check: headerless callers are rejected on every internal endpoint.
    async with httpx.AsyncClient(transport=transport) as raw_client:
        for method, path in (
            ("POST", f"http://127.0.0.1:{TOOL_SERVER_PORT}/notify_config_changed"),
            ("POST", f"http://127.0.0.1:{MEMORY_SERVER_PORT}/reload"),
            ("GET", f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins"),
            ("POST", f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/tools/register"),
            ("POST", f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/api/llm-tools/callback/p/t"),
        ):
            assert (await raw_client.request(method, path)).status_code == 401
    assert recorded == []

    real_async_client = httpx.AsyncClient

    class _ASGIRoutingAsyncClient(real_async_client):
        """Route caller requests to the in-process ASGI app while keeping caller event_hooks untouched."""

        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _ASGIRoutingAsyncClient)

    # 1. characters_router/notify.py -> POST /reload
    from main_routers.characters_router import notify as notify_mod

    assert await notify_mod.notify_memory_server_reload(reason="proxy-test") is True
    assert recorded[-1] == ("POST", "/reload", True)

    # 2. app/agent_server/api_routes.py -> GET /plugins
    from app.agent_server import api_routes

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(api_routes.asyncio, "sleep", _fast_sleep)
    readiness = await api_routes._check_user_plugin_registry_readiness()
    assert readiness["status"] == "empty"
    assert recorded[-1] == ("GET", "/plugins", True)

    # 3. plugin/server/messaging/llm_tool_registry.py -> POST /api/tools/register
    from plugin.server.messaging import llm_tool_registry

    monkeypatch.setattr(llm_tool_registry, "_HTTP_CLIENT", None)
    try:
        reg_resp = await llm_tool_registry.register_remote_tool(
            plugin_id="p",
            name="t",
            description="desc",
            parameters={"type": "object", "properties": {}},
            timeout_seconds=10.0,
        )
        assert reg_resp["ok"] is True
        assert recorded[-1] == ("POST", "/api/tools/register", True)
    finally:
        await llm_tool_registry.shutdown_http_client()
        llm_tool_registry._HTTP_CLIENT = None

    # 4. main_routers/tool_router.py -> POST callback_url
    from main_logic.tool_calling import ToolCall
    from main_routers import tool_router

    monkeypatch.setattr(tool_router, "_HTTP_CLIENT", None)
    try:
        tool_res = await tool_router._remote_dispatch(
            ToolCall(name="t", arguments={"x": 1}, call_id="c1"),
            {"source": "plugin:p", "callback_url": f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/api/llm-tools/callback/p/t"},
        )
        assert tool_res.is_error is False
        assert tool_res.output == {"ran": True}
        assert recorded[-1] == ("POST", "/api/llm-tools/callback/p/t", True)
    finally:
        if tool_router._HTTP_CLIENT is not None:
            await tool_router._HTTP_CLIENT.aclose()
        tool_router._HTTP_CLIENT = None

    # 5. main_routers/config_router/core_config.py -> POST /notify_config_changed
    import asyncio
    from utils import config_manager as config_manager_module
    from main_routers.config_router import core_config

    manager = config_manager_module.get_config_manager("N.E.K.O")
    manager.config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager_module, "get_config_manager", lambda *a, **k: manager)
    manager.save_json_config(
        "core_config.json",
        {"coreApi": "qwen", "assistApi": "qwen", "coreApiKey": "old-key", "enableCustomApi": True},
    )
    manager._core_config_cache = None

    async def _noop(*_args, **_kwargs):
        return None

    class _FakeCoreRequest:
        async def json(self):
            return {"coreApi": "qwen", "assistApi": "qwen", "enableCustomApi": True, "coreApiKey": "new-key"}

    monkeypatch.setattr(core_config, "get_session_manager", lambda: {})
    monkeypatch.setattr(core_config, "get_initialize_character_data", lambda: _noop)
    monkeypatch.setattr(core_config, "ensure_default_yui_voice_for_free_api", _noop)
    monkeypatch.setattr(core_config, "_auto_resolve_provider_urls_for_save", _noop)
    monkeypatch.setattr(core_config, "_core_api_save_lock", asyncio.Lock())

    cfg_res = await core_config.update_core_config(_FakeCoreRequest())
    assert cfg_res["success"] is True
    assert recorded[-1] == ("POST", "/notify_config_changed", True)


_INTERNAL_HTTPX_SCAN_ROOTS = (
    "app",
    "main_logic",
    "main_routers",
    "memory",
    "nekopower",
    "plugin/server",
    "plugin/sdk",
    "plugin/plugins",
    "services",
    "utils",
)
_INTERNAL_HTTPX_MODULE_INDICATORS = (
    "MAIN_SERVER_PORT",
    "MEMORY_SERVER_PORT",
    "TOOL_SERVER_PORT",
    "USER_PLUGIN_SERVER_PORT",
    "USER_PLUGIN_BASE",
    "resolve_user_plugin_base",
    "_main_server_port",
    "_main_server_base_url",
    "plugin_server_url",
    "_plugin_server_base",
    "_agent_base",
    "_main_base",
    "48911",
    "48912",
    "48915",
    "48916",
)
_INTERNAL_HTTPX_FUNC_INDICATORS = _INTERNAL_HTTPX_MODULE_INDICATORS + ("127.0.0.1", "localhost")
_INTERNAL_HTTPX_HOOK_HELPERS = (
    "internal_service_event_hooks",
    "merge_internal_service_event_hooks",
    "attach_internal_service_proof",
    "attach_internal_service_proof_sync",
)

# Explicitly documented non-internal / special-auth httpx clients that coexist with local port constants.
_INTERNAL_HTTPX_EXEMPT_SITES = {
    ("app/main_server/preload.py", "_sync_preload_modules._warmup_httpx"),
    ("app/main_server/web_app.py", "proxy_user_plugin_market_bridge"),
    ("main_routers/characters_router/voice_cloning.py", "voice_clone"),
    ("main_routers/community_oauth.py", "_refresh_oauth_token"),
    ("main_routers/community_oauth.py", "_exchange_oauth_code"),
    ("main_routers/community_oauth.py", "_bootstrap_session"),
    ("main_routers/community_oauth.py", "_oauth_guest_bind"),
    ("main_routers/community_oauth.py", "_revoke_tokens_best_effort"),
    ("plugin/server/routes/knowledge_market.py", "_fetch_version_descriptor"),
    ("plugin/server/routes/knowledge_market.py", "_download"),
    ("plugin/server/routes/knowledge_market.py", "_report_subscription_best_effort"),
    ("plugin/server/routes/knowledge_market.py", "_report_unsubscribe_best_effort"),
    ("plugin/server/routes/market_bridge.py", "_proxy_market_catalog"),
    ("plugin/server/routes/market_bridge.py", "_proxy_market_authenticated"),
    ("plugin/server/routes/market_bridge.py", "market_feedback_avatars"),
    ("plugin/server/routes/market_bridge.py", "_measure_github_proxy_sources.probe"),
    ("plugin/server/routes/market_bridge.py", "_market_catalog_client"),
    ("plugin/server/routes/market_bridge.py", "_exchange_oauth_code"),
    ("plugin/server/routes/market_bridge.py", "_refresh_oauth_token"),
    ("plugin/server/routes/market_bridge.py", "_revoke_oauth_token_best_effort"),
    ("plugin/server/routes/market_bridge.py", "_fetch_auth_userinfo"),
    ("plugin/server/routes/market_bridge.py", "_probe_market_user"),
    ("plugin/server/routes/market_bridge.py", "_report_market_install"),
}


def _iter_ast_calls(tree: ast.AST):
    stack: list[ast.AST] = []

    def _visit(node: ast.AST):
        pushed = False
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.append(node)
            pushed = True
        elif isinstance(node, ast.Call):
            yield node, list(stack)
        for child in ast.iter_child_nodes(node):
            yield from _visit(child)
        if pushed:
            stack.pop()

    return _visit(tree)


def _collect_httpx_import_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    mod_aliases = {"httpx"}
    client_aliases = {"AsyncClient", "Client"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "httpx":
                    mod_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "httpx":
            for alias in node.names:
                if alias.name in ("AsyncClient", "Client"):
                    client_aliases.add(alias.asname or alias.name)
    return mod_aliases, client_aliases


_INTERNAL_HTTPX_CALLABLE_HOOK_HELPERS = frozenset(
    {
        "attach_internal_service_proof",
        "attach_internal_service_proof_sync",
    }
)


def _callee_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _expr_is_hook_callable(node: ast.AST | None, hook_helpers: tuple[str, ...]) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = node.id if isinstance(node, ast.Name) else node.attr
        return name in hook_helpers and name in _INTERNAL_HTTPX_CALLABLE_HOOK_HELPERS
    if isinstance(node, ast.IfExp):
        return _expr_is_hook_callable(node.body, hook_helpers) and _expr_is_hook_callable(node.orelse, hook_helpers)
    if isinstance(node, ast.NamedExpr):
        return _expr_is_hook_callable(node.value, hook_helpers)
    return False


def _request_hook_seq_has_proof(node: ast.AST | None, hook_helpers: tuple[str, ...]) -> bool:
    if node is None:
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_expr_is_hook_callable(el, hook_helpers) for el in node.elts)
    if isinstance(node, ast.IfExp):
        return _request_hook_seq_has_proof(node.body, hook_helpers) and _request_hook_seq_has_proof(
            node.orelse, hook_helpers
        )
    if isinstance(node, ast.NamedExpr):
        return _request_hook_seq_has_proof(node.value, hook_helpers)
    return False


def _expr_uses_hook_helper(node: ast.AST | None, hook_helpers: tuple[str, ...]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Call):
        name = _callee_name(node.func)
        return name is not None and name in hook_helpers and name not in _INTERNAL_HTTPX_CALLABLE_HOOK_HELPERS
    if isinstance(node, ast.Dict):
        provides = False
        for k, v in zip(node.keys, node.values):
            if k is None:
                provides = _expr_uses_hook_helper(v, hook_helpers)
            elif isinstance(k, ast.Constant) and k.value == "request":
                provides = _request_hook_seq_has_proof(v, hook_helpers)
        return provides
    if isinstance(node, ast.IfExp):
        return _expr_uses_hook_helper(node.body, hook_helpers) and _expr_uses_hook_helper(node.orelse, hook_helpers)
    if isinstance(node, ast.BoolOp):
        return all(_expr_uses_hook_helper(val, hook_helpers) for val in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        if _expr_uses_hook_helper(node.right, hook_helpers):
            return True
        if isinstance(node.right, ast.Dict) and not any(
            k is None or (isinstance(k, ast.Constant) and k.value == "request") for k in node.right.keys
        ):
            return _expr_uses_hook_helper(node.left, hook_helpers)
        return False
    if isinstance(node, ast.NamedExpr):
        return _expr_uses_hook_helper(node.value, hook_helpers)
    return False


def _merge_dict_into_binding(dict_node: ast.Dict, bound: bool, hook_helpers: tuple[str, ...]) -> bool:
    for k, v in zip(dict_node.keys, dict_node.values):
        if k is None:
            bound = _merge_dict_into_binding(v, bound, hook_helpers) if isinstance(v, ast.Dict) else False
        elif isinstance(k, ast.Constant) and k.value == "event_hooks":
            bound = _expr_uses_hook_helper(v, hook_helpers)
    return bound


def _dict_expr_binds_event_hooks(node: ast.AST, hook_helpers: tuple[str, ...]) -> bool:
    return isinstance(node, ast.Dict) and _merge_dict_into_binding(node, False, hook_helpers)


def _stmt_contains_node(stmt: ast.AST, target: ast.AST) -> bool:
    return any(node is target for node in ast.walk(stmt))


def _is_httpx_client_call(
    node: ast.AST,
    httpx_mod_aliases: set[str],
    httpx_client_aliases: set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in httpx_mod_aliases
        and node.func.attr in ("AsyncClient", "Client")
    ):
        return True
    return isinstance(node.func, ast.Name) and node.func.id in httpx_client_aliases


def _stmt_mentions_name(
    node: ast.AST,
    var_name: str,
    httpx_mod_aliases: set[str] | None = None,
    httpx_client_aliases: set[str] | None = None,
) -> bool:
    if isinstance(node, ast.Name):
        return node.id == var_name
    if (
        httpx_mod_aliases is not None
        and httpx_client_aliases is not None
        and _is_httpx_client_call(node, httpx_mod_aliases, httpx_client_aliases)
    ):
        assert isinstance(node, ast.Call)
        if any(_stmt_mentions_name(arg, var_name, httpx_mod_aliases, httpx_client_aliases) for arg in node.args):
            return True
        for kw in node.keywords:
            if kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id == var_name:
                continue
            if _stmt_mentions_name(kw.value, var_name, httpx_mod_aliases, httpx_client_aliases):
                return True
        return False
    return any(
        _stmt_mentions_name(child, var_name, httpx_mod_aliases, httpx_client_aliases)
        for child in ast.iter_child_nodes(node)
    )


def _control_flow_node_types() -> tuple[type[ast.AST], ...]:
    nodes: list[type[ast.AST]] = [ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try]
    for attr in ("TryStar", "Match"):
        cls = getattr(ast, attr, None)
        if cls is not None:
            nodes.append(cls)
    return tuple(nodes)


def _contains_direct_continue(node: ast.AST) -> bool:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Continue):
            return True
        if isinstance(child, (ast.For, ast.AsyncFor, ast.While, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if _contains_direct_continue(child):
            return True
    return False


def _apply_expr_mutations(
    node: ast.AST,
    var_name: str,
    bound: bool,
    hook_helpers: tuple[str, ...],
) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return bound
    for child in ast.iter_child_nodes(node):
        bound = _apply_expr_mutations(child, var_name, bound, hook_helpers)
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name) and node.target.id == var_name:
        return _dict_expr_binds_event_hooks(node.value, hook_helpers)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == var_name
    ):
        attr = node.func.attr
        if attr in ("clear", "popitem"):
            return False
        if attr == "pop":
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value != "event_hooks"
            ):
                return bound
            return False
        if attr == "update":
            if node.args:
                first_arg = node.args[0]
                bound = (
                    _merge_dict_into_binding(first_arg, bound, hook_helpers)
                    if isinstance(first_arg, ast.Dict)
                    else False
                )
            for ukw in node.keywords:
                if ukw.arg is None:
                    bound = (
                        _merge_dict_into_binding(ukw.value, bound, hook_helpers)
                        if isinstance(ukw.value, ast.Dict)
                        else False
                    )
                elif ukw.arg == "event_hooks":
                    bound = _expr_uses_hook_helper(ukw.value, hook_helpers)
            return bound
    return bound


def _update_event_hooks_binding_state(
    stmt: ast.stmt,
    var_name: str,
    bound: bool,
    hook_helpers: tuple[str, ...],
) -> bool:
    if isinstance(stmt, ast.Assign):
        bound = _apply_expr_mutations(stmt.value, var_name, bound, hook_helpers)
        for tgt in stmt.targets:
            if (
                isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name)
                and tgt.value.id == var_name
            ):
                if isinstance(tgt.slice, ast.Constant) and isinstance(tgt.slice.value, str):
                    if tgt.slice.value == "event_hooks":
                        bound = _expr_uses_hook_helper(stmt.value, hook_helpers)
                else:
                    bound = False
            elif isinstance(tgt, ast.Name) and tgt.id == var_name:
                bound = _dict_expr_binds_event_hooks(stmt.value, hook_helpers)
        return bound

    if isinstance(stmt, ast.AnnAssign):
        if stmt.value is not None:
            bound = _apply_expr_mutations(stmt.value, var_name, bound, hook_helpers)
        tgt = stmt.target
        if (
            isinstance(tgt, ast.Subscript)
            and isinstance(tgt.value, ast.Name)
            and tgt.value.id == var_name
        ):
            if isinstance(tgt.slice, ast.Constant) and isinstance(tgt.slice.value, str):
                if tgt.slice.value == "event_hooks":
                    return stmt.value is not None and _expr_uses_hook_helper(stmt.value, hook_helpers)
                return bound
            return False
        if isinstance(tgt, ast.Name) and tgt.id == var_name:
            return stmt.value is not None and _dict_expr_binds_event_hooks(stmt.value, hook_helpers)
        return bound

    if isinstance(stmt, ast.Delete):
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name) and tgt.id == var_name:
                return False
            if (
                isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name)
                and tgt.value.id == var_name
                and not (
                    isinstance(tgt.slice, ast.Constant)
                    and isinstance(tgt.slice.value, str)
                    and tgt.slice.value != "event_hooks"
                )
            ):
                return False
        return bound

    if isinstance(stmt, ast.AugAssign):
        bound = _apply_expr_mutations(stmt.value, var_name, bound, hook_helpers)
        if isinstance(stmt.target, ast.Name) and stmt.target.id == var_name:
            if isinstance(stmt.op, ast.BitOr) and isinstance(stmt.value, ast.Dict):
                return _merge_dict_into_binding(stmt.value, bound, hook_helpers)
            return False
        return bound

    return _apply_expr_mutations(stmt, var_name, bound, hook_helpers)


def _loop_backedge_preserves_binding(
    stmts: list[ast.stmt],
    var_name: str,
    initial_bound: bool,
    hook_helpers: tuple[str, ...],
    httpx_mod_aliases: set[str],
    httpx_client_aliases: set[str],
) -> bool:
    if not initial_bound:
        return False
    control_flow_nodes = _control_flow_node_types()
    cur = True
    cont_ok = True
    for stmt in stmts:
        if isinstance(stmt, ast.Continue):
            return cont_ok and cur
        if isinstance(stmt, (ast.Break, ast.Return, ast.Raise)):
            return cont_ok
        if isinstance(stmt, control_flow_nodes):
            if _stmt_mentions_name(stmt, var_name, httpx_mod_aliases, httpx_client_aliases):
                if not isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)) and _contains_direct_continue(stmt):
                    cont_ok = False
                cur = False
            elif not isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)) and _contains_direct_continue(stmt):
                cont_ok = cont_ok and cur
            continue
        cur = _update_event_hooks_binding_state(stmt, var_name, cur, hook_helpers)
    return cur and cont_ok


def _track_binding_in_block(
    stmts: list[ast.stmt],
    target_call: ast.Call,
    var_name: str,
    bound: bool,
    hook_helpers: tuple[str, ...],
    httpx_mod_aliases: set[str],
    httpx_client_aliases: set[str],
) -> tuple[bool, bool]:
    control_flow_nodes = _control_flow_node_types()

    for stmt in stmts:
        if _stmt_contains_node(stmt, target_call):
            child_blocks: list[tuple[list[ast.stmt], bool]] = []
            loop_bound = bound
            if isinstance(stmt, ast.If):
                child_blocks.extend([(stmt.body, bound), (stmt.orelse, bound)])
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                loop_bound = bound and _loop_backedge_preserves_binding(
                    stmt.body,
                    var_name,
                    bound,
                    hook_helpers,
                    httpx_mod_aliases,
                    httpx_client_aliases,
                )
                child_blocks.extend([(stmt.body, loop_bound), (stmt.orelse, loop_bound)])
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                child_blocks.append((stmt.body, bound))
            elif isinstance(stmt, control_flow_nodes):
                body_stmts = getattr(stmt, "body", [])
                body_mentions = any(
                    _stmt_mentions_name(s, var_name, httpx_mod_aliases, httpx_client_aliases) for s in body_stmts
                )
                child_blocks.append((body_stmts, bound))
                for handler in getattr(stmt, "handlers", []):
                    child_blocks.append((handler.body, bound and not body_mentions))
                orelse_stmts = getattr(stmt, "orelse", [])
                child_blocks.append((orelse_stmts, bound and not body_mentions))
                final_stmts = getattr(stmt, "finalbody", [])
                child_blocks.append((final_stmts, bound and not body_mentions))
                for case in getattr(stmt, "cases", []):
                    child_blocks.append((case.body, bound))

            for block, initial_bound in child_blocks:
                if any(_stmt_contains_node(s, target_call) for s in block):
                    return _track_binding_in_block(
                        block,
                        target_call,
                        var_name,
                        initial_bound,
                        hook_helpers,
                        httpx_mod_aliases,
                        httpx_client_aliases,
                    )
            return True, (loop_bound if isinstance(stmt, ast.While) else bound)

        if isinstance(stmt, control_flow_nodes):
            if _stmt_mentions_name(stmt, var_name, httpx_mod_aliases, httpx_client_aliases):
                bound = False
            continue

        bound = _update_event_hooks_binding_state(stmt, var_name, bound, hook_helpers)

    return False, bound


def _kw_splat_sets_event_hooks(
    call: ast.Call,
    func_node: ast.AST | None,
    hook_helpers: tuple[str, ...],
    httpx_mod_aliases: set[str],
    httpx_client_aliases: set[str],
) -> bool:
    for kw in call.keywords:
        if kw.arg is not None:
            continue
        if _dict_expr_binds_event_hooks(kw.value, hook_helpers):
            return True
        if isinstance(kw.value, ast.Name) and isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found, bound = _track_binding_in_block(
                func_node.body,
                call,
                kw.value.id,
                False,
                hook_helpers,
                httpx_mod_aliases,
                httpx_client_aliases,
            )
            if found and bound:
                return True
    return False


def _should_skip_internal_httpx_scan_path(path: Path, repo_root: Path) -> bool:
    """Skip tests/ and __pycache__/ under repo_root (ignoring parent checkout path) plus test_*.py files."""
    rel_obj = path.relative_to(repo_root)
    return any(part in ("tests", "__pycache__") for part in rel_obj.parts[:-1]) or rel_obj.name.startswith("test_")


def _scan_source_for_internal_httpx_clients(
    source: str,
    relpath: str,
    *,
    module_indicators: tuple[str, ...] = _INTERNAL_HTTPX_MODULE_INDICATORS,
    func_indicators: tuple[str, ...] = _INTERNAL_HTTPX_FUNC_INDICATORS,
    hook_helpers: tuple[str, ...] = _INTERNAL_HTTPX_HOOK_HELPERS,
    exempt_sites: set[tuple[str, str]] = _INTERNAL_HTTPX_EXEMPT_SITES,
) -> tuple[int, set[tuple[str, str]], list[str]]:
    if "httpx" not in source and "AsyncClient" not in source and "Client(" not in source:
        return 0, set(), []

    tree = ast.parse(source, filename=relpath)
    httpx_mod_aliases, httpx_client_aliases = _collect_httpx_import_bindings(tree)
    mod_has_indicator = any(ind in source for ind in module_indicators)

    signed_count = 0
    seen_exemptions: set[tuple[str, str]] = set()
    unhooked: list[str] = []

    for call, owners in _iter_ast_calls(tree):
        if not _is_httpx_client_call(call, httpx_mod_aliases, httpx_client_aliases):
            continue

        qualname = ".".join(getattr(o, "name", "") for o in owners) or "<module>"
        func_node = next(
            (o for o in reversed(owners) if isinstance(o, (ast.FunctionDef, ast.AsyncFunctionDef))),
            None,
        )
        func_source = (ast.get_source_segment(source, func_node) or "") if func_node else ""
        if not mod_has_indicator and not any(ind in func_source for ind in func_indicators):
            continue

        has_hook = any(
            kw.arg == "event_hooks" and _expr_uses_hook_helper(kw.value, hook_helpers)
            for kw in call.keywords
        )
        if not has_hook:
            has_hook = _kw_splat_sets_event_hooks(
                call,
                func_node,
                hook_helpers,
                httpx_mod_aliases,
                httpx_client_aliases,
            )

        site_key = (relpath, qualname)
        if has_hook:
            signed_count += 1
        elif site_key in exempt_sites:
            seen_exemptions.add(site_key)
        else:
            unhooked.append(f"{relpath}:{call.lineno} ({qualname})")

    return signed_count, seen_exemptions, unhooked


def test_ast_guard_all_internal_loopback_httpx_clients_sign_requests():
    """Static guard: every httpx client in an internal-service caller path must attach internal-service hooks."""
    repo_root = Path(__file__).resolve().parents[2]
    signed_count = 0
    seen_exemptions: set[tuple[str, str]] = set()
    unhooked: list[str] = []

    for root_rel in _INTERNAL_HTTPX_SCAN_ROOTS:
        root_dir = repo_root / root_rel
        for path in sorted(root_dir.rglob("*.py")):
            if _should_skip_internal_httpx_scan_path(path, repo_root):
                continue
            source = path.read_text(encoding="utf-8")
            relpath = path.relative_to(repo_root).as_posix()
            site_signed, site_exemptions, site_unhooked = _scan_source_for_internal_httpx_clients(
                source,
                relpath,
            )
            signed_count += site_signed
            seen_exemptions |= site_exemptions
            unhooked.extend(site_unhooked)

    assert signed_count >= 15, f"expected >= 15 signed internal httpx sites, found {signed_count}"
    assert not unhooked, f"unsigned httpx client(s) in internal-service paths: {unhooked}"
    stale_exemptions = _INTERNAL_HTTPX_EXEMPT_SITES - seen_exemptions
    assert not stale_exemptions, f"stale exemption(s) in exempt_sites: {sorted(stale_exemptions)}"


def test_internal_httpx_ast_guard_catches_aliased_and_discarded_hook_calls():
    """AST guard must flag aliased httpx constructors without hooks and discarded-hook **kwargs splats."""
    aliased_client_src = (
        "from httpx import AsyncClient as AsyncHTTPClient\n"
        "from config import MAIN_SERVER_PORT\n"
        "async def call():\n"
        "    async with AsyncHTTPClient(timeout=5) as c:\n"
        "        return await c.get(f'http://127.0.0.1:{MAIN_SERVER_PORT}/x')\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(aliased_client_src, "synthetic/alias_client.py")
    assert signed == 0
    assert unhooked == ["synthetic/alias_client.py:4 (call)"]

    aliased_mod_src = (
        "import httpx as hx\n"
        "from config import MAIN_SERVER_PORT\n"
        "async def call():\n"
        "    async with hx.AsyncClient(timeout=5) as c:\n"
        "        return await c.get(f'http://127.0.0.1:{MAIN_SERVER_PORT}/x')\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(aliased_mod_src, "synthetic/alias_mod.py")
    assert signed == 0
    assert unhooked == ["synthetic/alias_mod.py:4 (call)"]

    discarded_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    _ = internal_service_event_hooks()\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(discarded_splat_src, "synthetic/discarded_splat.py")
    assert signed == 0
    assert unhooked == ["synthetic/discarded_splat.py:6 (make)"]

    bound_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import merge_internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    merged = dict(kwargs)\n"
        "    merged['event_hooks'] = merge_internal_service_event_hooks(merged.get('event_hooks'))\n"
        "    return httpx.AsyncClient(**merged)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(bound_splat_src, "synthetic/bound_splat.py")
    assert signed == 1
    assert unhooked == []

    post_bind_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    client = httpx.AsyncClient(**kwargs)\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    return client\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(post_bind_splat_src, "synthetic/post_bind.py")
    assert signed == 0
    assert unhooked == ["synthetic/post_bind.py:5 (make)"]

    overwritten_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import merge_internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    merged = dict(kwargs)\n"
        "    merged['event_hooks'] = merge_internal_service_event_hooks(merged.get('event_hooks'))\n"
        "    merged['event_hooks'] = {}\n"
        "    return httpx.AsyncClient(**merged)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        overwritten_splat_src, "synthetic/overwritten_splat.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/overwritten_splat.py:8 (make)"]

    setdefault_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs.setdefault('event_hooks', internal_service_event_hooks())\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        setdefault_splat_src, "synthetic/setdefault_splat.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/setdefault_splat.py:6 (make)"]

    augassign_splat_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    kwargs |= {'event_hooks': {}}\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        augassign_splat_src, "synthetic/augassign_splat.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/augassign_splat.py:7 (make)"]

    conditional_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(enable_signing: bool = False):\n"
        "    return httpx.AsyncClient(event_hooks=internal_service_event_hooks() if enable_signing else {})\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        conditional_neg_src, "synthetic/conditional_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/conditional_neg.py:5 (make)"]

    boolop_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(caller_hooks=None):\n"
        "    return httpx.AsyncClient(event_hooks=caller_hooks or internal_service_event_hooks())\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        boolop_neg_src, "synthetic/boolop_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/boolop_neg.py:5 (make)"]

    dict_callable_cond_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import attach_internal_service_proof\n"
        "def noop(req): pass\n"
        "def make(enable: bool = False):\n"
        "    return httpx.AsyncClient(event_hooks={'request': [attach_internal_service_proof if enable else noop]})\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        dict_callable_cond_neg_src, "synthetic/dict_callable_cond_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/dict_callable_cond_neg.py:6 (make)"]

    conditional_pos_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(is_sync: bool = False):\n"
        "    return httpx.AsyncClient(event_hooks=internal_service_event_hooks(sync=True) if is_sync else internal_service_event_hooks())\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        conditional_pos_src, "synthetic/conditional_pos.py"
    )
    assert signed == 1
    assert unhooked == []

    dict_callable_pos_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import attach_internal_service_proof\n"
        "def make():\n"
        "    return httpx.AsyncClient(event_hooks={'request': [attach_internal_service_proof]})\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        dict_callable_pos_src, "synthetic/dict_callable_pos.py"
    )
    assert signed == 1
    assert unhooked == []

    loop_clear_after_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(items, **kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    for _item in items:\n"
        "        client = httpx.AsyncClient(**kwargs)\n"
        "        kwargs.clear()\n"
        "    return client\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        loop_clear_after_src, "synthetic/loop_clear_after.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/loop_clear_after.py:7 (make)"]

    loop_continue_clear_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(items, **kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    for item in items:\n"
        "        client = httpx.AsyncClient(**kwargs)\n"
        "        if item:\n"
        "            kwargs.clear()\n"
        "            continue\n"
        "        kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    return client\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        loop_continue_clear_src, "synthetic/loop_continue_clear.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/loop_continue_clear.py:7 (make)"]

    loop_orelse_clear_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(items, **kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    for _item in items:\n"
        "        kwargs.clear()\n"
        "    else:\n"
        "        return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        loop_orelse_clear_src, "synthetic/loop_orelse_clear.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/loop_orelse_clear.py:9 (make)"]

    loop_rebind_before_construct_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(items, **kwargs):\n"
        "    for _item in items:\n"
        "        kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "        client = httpx.AsyncClient(**kwargs)\n"
        "        kwargs.clear()\n"
        "    return client\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        loop_rebind_before_construct_src, "synthetic/loop_rebind_before.py"
    )
    assert signed == 1
    assert unhooked == []

    loop_unmodified_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "async def make(urls, **kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    for url in urls:\n"
        "        async with httpx.AsyncClient(**kwargs) as client:\n"
        "            await client.get(url)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        loop_unmodified_src, "synthetic/loop_unmodified.py"
    )
    assert signed == 1
    assert unhooked == []

    assigned_pop_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    _removed = kwargs.pop('event_hooks')\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        assigned_pop_neg_src, "synthetic/assigned_pop_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/assigned_pop_neg.py:7 (make)"]

    dynamic_pop_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(k, **kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    kwargs.pop(k)\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        dynamic_pop_neg_src, "synthetic/dynamic_pop_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/dynamic_pop_neg.py:7 (make)"]

    popitem_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    kwargs.popitem()\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        popitem_neg_src, "synthetic/popitem_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/popitem_neg.py:7 (make)"]

    walrus_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    _ = (kwargs := {})\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        walrus_neg_src, "synthetic/walrus_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/walrus_neg.py:7 (make)"]

    augassign_splat_override_pos_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(base, **kwargs):\n"
        "    kwargs |= {**base, 'event_hooks': internal_service_event_hooks()}\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        augassign_splat_override_pos_src, "synthetic/augassign_splat_override_pos.py"
    )
    assert signed == 1
    assert unhooked == []

    augassign_splat_override_neg_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(base, **kwargs):\n"
        "    kwargs |= {'event_hooks': internal_service_event_hooks(), **base}\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        augassign_splat_override_neg_src, "synthetic/augassign_splat_override_neg.py"
    )
    assert signed == 0
    assert unhooked == ["synthetic/augassign_splat_override_neg.py:6 (make)"]

    pop_other_pos_src = (
        "import httpx\n"
        "from config import MAIN_SERVER_PORT\n"
        "from utils.internal_http_client import internal_service_event_hooks\n"
        "def make(**kwargs):\n"
        "    kwargs['event_hooks'] = internal_service_event_hooks()\n"
        "    _timeout = kwargs.pop('timeout', None)\n"
        "    return httpx.AsyncClient(**kwargs)\n"
    )
    signed, _, unhooked = _scan_source_for_internal_httpx_clients(
        pop_other_pos_src, "synthetic/pop_other_pos.py"
    )
    assert signed == 1
    assert unhooked == []


def test_internal_httpx_scan_path_filter_uses_repo_relative_components(tmp_path: Path):
    """Scanning must ignore 'tests'/'__pycache__' above repo_root while filtering test dirs/files inside repo_root."""
    fake_checkout = tmp_path / "tests" / "neko"
    source_file = fake_checkout / "utils" / "client.py"
    in_repo_test_dir = fake_checkout / "plugin" / "tests" / "helper.py"
    in_repo_pycache = fake_checkout / "utils" / "__pycache__" / "mod.py"
    in_repo_test_file = fake_checkout / "utils" / "test_client.py"

    assert _should_skip_internal_httpx_scan_path(source_file, fake_checkout) is False
    assert _should_skip_internal_httpx_scan_path(in_repo_test_dir, fake_checkout) is True
    assert _should_skip_internal_httpx_scan_path(in_repo_pycache, fake_checkout) is True
    assert _should_skip_internal_httpx_scan_path(in_repo_test_file, fake_checkout) is True


async def test_untrusted_loopback_ports_do_not_receive_proof_while_dynamic_plugin_port_is_trusted(
    monkeypatch: pytest.MonkeyPatch,
):
    """Untrusted arbitrary local ports must never receive X-Neko-Internal-Service; dynamic plugin port is honored."""
    _clear_deployment_mode_env(monkeypatch)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.delenv("NEKO_USER_PLUGIN_SERVER_PORT", raising=False)
    monkeypatch.setattr(config, "MAIN_SERVER_PORT", 48911, raising=False)
    monkeypatch.setattr(config, "MEMORY_SERVER_PORT", 48912, raising=False)
    monkeypatch.setattr(config, "TOOL_SERVER_PORT", 48915, raising=False)
    monkeypatch.setattr(config, "USER_PLUGIN_SERVER_PORT", 48916, raising=False)
    monkeypatch.setattr(config, "USER_PLUGIN_BASE", "http://127.0.0.1:48916", raising=False)

    arbitrary_req = httpx.Request("POST", "http://127.0.0.1:59999/arbitrary-callback")
    assert _should_attach_internal_service_proof(arbitrary_req) is False

    dynamic_req = httpx.Request("POST", "http://127.0.0.1:59998/api/llm-tools/callback/p/t")
    assert _should_attach_internal_service_proof(dynamic_req) is False

    seen_headers: list[dict[str, str]] = []
    mock_transport = httpx.MockTransport(
        lambda req: (seen_headers.append(dict(req.headers)), httpx.Response(200, json={"ok": True}))[1]
    )
    async with httpx.AsyncClient(
        transport=mock_transport,
        event_hooks=internal_service_event_hooks(),
    ) as client:
        resp = await client.post("http://127.0.0.1:59999/arbitrary-callback")
        assert resp.status_code == 200
    assert seen_headers and INTERNAL_SERVICE_HEADER not in seen_headers[0]

    # When launcher publishes a dynamically bound plugin port, that port becomes trusted.
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "59998")
    assert _should_attach_internal_service_proof(dynamic_req) is True
    assert _should_attach_internal_service_proof(httpx.Request("POST", "http://127.0.0.1:59999/other")) is False


async def test_signing_failure_raises_request_error_and_agent_router_rolls_back_switches(
    monkeypatch: pytest.MonkeyPatch,
):
    """Signing hook failures raise InternalServiceSigningError (httpx.RequestError) and trigger take_back; ReadTimeout stays taken."""
    from main_routers import agent_router
    import utils.instance_access as access

    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    req = httpx.Request("POST", f"http://127.0.0.1:{TOOL_SERVER_PORT}/agent/command")

    for err in (OSError("unreadable key"), ValueError("short key")):
        monkeypatch.setattr(access, "instance_key", lambda exc=err: (_ for _ in ()).throw(exc))
        with pytest.raises(InternalServiceSigningError) as async_exc:
            await attach_internal_service_proof(httpx.Request("POST", req.url))
        assert isinstance(async_exc.value, httpx.RequestError)
        assert isinstance(async_exc.value, httpx.HTTPError)

        with pytest.raises(InternalServiceSigningError) as sync_exc:
            attach_internal_service_proof_sync(httpx.Request("POST", req.url))
        assert isinstance(sync_exc.value, httpx.RequestError)

    class _Manager:
        def __init__(self) -> None:
            self.agent_flags: dict = {"agent_enabled": False, "user_plugin_enabled": False}

        def update_agent_flags(self, flags: dict) -> None:
            self.agent_flags.update(flags)

    class _Config:
        def get_character_data(self):
            return (None, "Lan", None, None, None, None, None, None, None)

        def is_agent_free(self) -> bool:
            return False

    class _Asked:
        async def json(self) -> dict:
            return {"command": "set_agent_enabled", "enabled": True}

    monkeypatch.setattr(access, "instance_key", lambda: (_ for _ in ()).throw(OSError("key unreadable")))
    manager_sign_fail = _Manager()
    transport_ok = httpx.MockTransport(lambda _r: httpx.Response(200, json={"success": True}))
    signing_client = httpx.AsyncClient(transport=transport_ok, event_hooks=internal_service_event_hooks())
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager_sign_fail})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: signing_client)

    try:
        answer = await agent_router.post_agent_command(_Asked())
    finally:
        await signing_client.aclose()
    assert answer.status_code == 502
    assert manager_sign_fail.agent_flags == {"agent_enabled": False, "user_plugin_enabled": False}

    # ReadTimeout remains treated as potentially delivered (taken=True, not rolled back).
    monkeypatch.setattr(access, "instance_key", lambda: KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    manager_timeout = _Manager()
    timeout_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r))),
        event_hooks=internal_service_event_hooks(),
    )
    monkeypatch.setattr(agent_router, "_remote_backend_block", lambda: None)
    monkeypatch.setattr(agent_router, "get_session_manager", lambda: {"Lan": manager_timeout})
    monkeypatch.setattr(agent_router, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(agent_router, "_get_http_client", lambda: timeout_client)
    try:
        timeout_answer = await agent_router.post_agent_command(_Asked())
    finally:
        await timeout_client.aclose()
    assert timeout_answer.status_code == 502
    assert manager_timeout.agent_flags == {"agent_enabled": True, "user_plugin_enabled": True}


@pytest.mark.parametrize(
    ("bad_server", "proof_port"),
    [
        (None, MAIN_SERVER_PORT),
        (("127.0.0.1", None), MAIN_SERVER_PORT),
        (("127.0.0.1", True), 1),
    ],
)
async def test_internal_service_proof_fails_closed_when_asgi_server_port_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    bad_server,
    proof_port: int,
):
    """_request_signing_port must fail closed when ASGI scope['server'] lacks an int port, never falling back to Host header."""
    _clear_deployment_mode_env(monkeypatch)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    valid_proof = internal_service_proof(KEY, "GET", "/private", port=proof_port)

    async def _send_with_server(
        server_value,
        *,
        proof: str = valid_proof,
        host_port: int = proof_port,
    ) -> int:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/private",
            "raw_path": b"/private",
            "query_string": b"",
            "headers": [
                (b"host", f"127.0.0.1:{host_port}".encode()),
                (INTERNAL_SERVICE_HEADER.lower().encode(), proof.encode()),
            ],
            "client": ("127.0.0.1", 50000),
        }
        if server_value is not None:
            scope["server"] = server_value

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        messages: list[dict] = []

        async def send(msg: dict):
            messages.append(msg)

        await app(scope, receive, send)
        start_msg = next(m for m in messages if m["type"] == "http.response.start")
        return start_msg["status"]

    main_proof = internal_service_proof(KEY, "GET", "/private", port=MAIN_SERVER_PORT)
    assert await _send_with_server(("127.0.0.1", MAIN_SERVER_PORT), proof=main_proof, host_port=MAIN_SERVER_PORT) == 200
    assert await _send_with_server(("127.0.0.1", proof_port)) == 200
    assert await _send_with_server(bad_server) == 401


async def test_task_tool_post_treats_signing_error_as_not_delivered(monkeypatch: pytest.MonkeyPatch):
    """ToolCallingMixin._post_to_task_system must treat InternalServiceSigningError as not delivered (None, False)."""
    from main_logic.core.tool_calling import ToolCallingMixin
    import utils.http.internal_client as internal_client_mod
    import utils.instance_access as access
    import utils.internal_http_client as internal_http_alias

    _clear_deployment_mode_env(monkeypatch)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setattr(access, "instance_key", lambda: (_ for _ in ()).throw(OSError("key unreadable")))

    transport_called = False

    def _handler(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, json={"ok": True})

    signing_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        event_hooks=internal_service_event_hooks(),
    )
    monkeypatch.setattr(internal_client_mod, "get_internal_http_client", lambda: signing_client)
    monkeypatch.setattr(internal_http_alias, "get_internal_http_client", lambda: signing_client)

    try:
        answer, may_have_arrived = await ToolCallingMixin._post_to_task_system(
            ToolCallingMixin(),
            "/agent/dispatch",
            {"task": "ping"},
        )
    finally:
        await signing_client.aclose()

    assert (answer, may_have_arrived) == (None, False)
    assert transport_called is False

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
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv(env_name, env_val)

    client = TestClient(_build_guarded_app(), base_url="http://127.0.0.1:48911", client=("127.0.0.1", 50000))

    # Headerless HTTP routes (GET and POST) must require credentials (401).
    assert client.get("/private").status_code == 401
    assert client.post("/private").status_code == 401

    # Headerless WebSocket on loopback (including /health) must also be rejected.
    with pytest.raises(Exception):
        with client.websocket_connect("ws://127.0.0.1:48911/socket"):
            pass
    with pytest.raises(Exception):
        with client.websocket_connect("ws://127.0.0.1:48911/health"):
            pass

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


def test_ast_guard_all_internal_loopback_httpx_clients_sign_requests():
    """Static guard: every httpx client in an internal-service caller path must attach internal-service hooks."""
    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = (
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
    module_indicators = (
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
    func_indicators = module_indicators + ("127.0.0.1", "localhost")
    hook_helpers = (
        "internal_service_event_hooks",
        "merge_internal_service_event_hooks",
        "attach_internal_service_proof",
        "attach_internal_service_proof_sync",
    )

    # Explicitly documented non-internal / special-auth httpx clients that coexist with local port constants.
    exempt_sites = {
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

    def _iter_calls(tree: ast.AST):
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

    signed_count = 0
    seen_exemptions: set[tuple[str, str]] = set()
    unhooked: list[str] = []

    for root_rel in scan_roots:
        root_dir = repo_root / root_rel
        for path in sorted(root_dir.rglob("*.py")):
            if any(part in ("tests", "__pycache__") for part in path.parts) or path.name.startswith("test_"):
                continue
            source = path.read_text(encoding="utf-8")
            if "AsyncClient" not in source and "Client(" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
            mod_has_indicator = any(ind in source for ind in module_indicators)
            relpath = path.relative_to(repo_root).as_posix()

            for call, owners in _iter_calls(tree):
                is_httpx_client = False
                if (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "httpx"
                    and call.func.attr in ("AsyncClient", "Client")
                ):
                    is_httpx_client = True
                elif isinstance(call.func, ast.Name) and call.func.id in ("AsyncClient", "Client"):
                    is_httpx_client = True
                if not is_httpx_client:
                    continue

                qualname = ".".join(getattr(o, "name", "") for o in owners) or "<module>"
                func_node = next(
                    (o for o in reversed(owners) if isinstance(o, (ast.FunctionDef, ast.AsyncFunctionDef))),
                    None,
                )
                func_source = (ast.get_source_segment(source, func_node) or "") if func_node else ""
                if not mod_has_indicator and not any(ind in func_source for ind in func_indicators):
                    continue

                has_hook = False
                has_kw_splat = any(kw.arg is None for kw in call.keywords)
                for kw in call.keywords:
                    if kw.arg == "event_hooks":
                        seg = ast.get_source_segment(source, kw.value) or ""
                        if any(helper in seg for helper in hook_helpers):
                            has_hook = True
                            break
                if not has_hook and has_kw_splat and any(helper in func_source for helper in hook_helpers):
                    has_hook = True

                site_key = (relpath, qualname)
                if has_hook:
                    signed_count += 1
                elif site_key in exempt_sites:
                    seen_exemptions.add(site_key)
                else:
                    unhooked.append(f"{relpath}:{call.lineno} ({qualname})")

    assert signed_count >= 15, f"expected >= 15 signed internal httpx sites, found {signed_count}"
    assert not unhooked, f"unsigned httpx client(s) in internal-service paths: {unhooked}"
    stale_exemptions = exempt_sites - seen_exemptions
    assert not stale_exemptions, f"stale exemption(s) in exempt_sites: {sorted(stale_exemptions)}"


async def test_untrusted_loopback_ports_do_not_receive_proof_while_dynamic_plugin_port_is_trusted(
    monkeypatch: pytest.MonkeyPatch,
):
    """Untrusted arbitrary local ports must never receive X-Neko-Internal-Service; dynamic plugin port is honored."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.delenv("NEKO_USER_PLUGIN_SERVER_PORT", raising=False)

    arbitrary_req = httpx.Request("POST", "http://127.0.0.1:59999/arbitrary-callback")
    assert _should_attach_internal_service_proof(arbitrary_req) is False

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
    dynamic_req = httpx.Request("POST", "http://127.0.0.1:59998/api/llm-tools/callback/p/t")
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


@pytest.mark.parametrize("bad_server", [None, ("127.0.0.1", None), ("127.0.0.1", True)])
async def test_internal_service_proof_fails_closed_when_asgi_server_port_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    bad_server,
):
    """_request_signing_port must fail closed when ASGI scope['server'] lacks an int port, never falling back to Host header."""
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", KEY)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")

    app = _build_guarded_app()
    valid_proof = internal_service_proof(KEY, "GET", "/private", port=MAIN_SERVER_PORT)

    async def _send_with_server(server_value) -> int:
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
                (b"host", f"127.0.0.1:{MAIN_SERVER_PORT}".encode()),
                (INTERNAL_SERVICE_HEADER.lower().encode(), valid_proof.encode()),
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

    assert await _send_with_server(("127.0.0.1", MAIN_SERVER_PORT)) == 200
    assert await _send_with_server(bad_server) == 401

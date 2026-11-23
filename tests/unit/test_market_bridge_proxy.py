from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from filelock import Timeout as FileLockTimeout

from app.main_server import web_app


@pytest.mark.asyncio
async def test_market_proxy_preserves_query_token_and_authorization(monkeypatch):
    import utils.instance_access as access

    key = "market-test-instance-key-" + "x" * 40
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", key)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setattr(access, "instance_key", lambda: pytest.fail("Desktop Market must not read/create an instance key"))
    seen: dict[str, object] = {}
    asgi_client = httpx.AsyncClient

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, content, headers):
            seen.update(method=method, url=url, content=content, headers=headers)
            return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    app = FastAPI()
    app.add_api_route(
        "/market/{path:path}",
        web_app.proxy_user_plugin_market_bridge,
        methods=["POST"],
    )
    async with asgi_client(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48911",
    ) as client:
        response = await client.post(
            "/market/oauth/start?token=query-token",
            headers={"Authorization": "Bearer header-token", "Origin": "http://localhost:48911"},
            content=b"{}",
        )

    assert response.status_code == 200
    assert seen["method"] == "POST"
    assert seen["url"] == "http://127.0.0.1:48916/market/oauth/start?token=query-token"
    assert seen["content"] == b"{}"
    forwarded_headers = seen["headers"]
    assert isinstance(forwarded_headers, dict)
    assert forwarded_headers["authorization"] == "Bearer header-token"
    assert forwarded_headers["origin"] == "http://localhost:48911"
    assert "x-neko-market-internal" not in forwarded_headers
    assert "x-neko-market-public-origin" not in forwarded_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,include_origin", [
    ("POST", "/market/oauth/start", True), ("GET", "/market/ordinary", False),
    ("GET", "/market/bridge-token", False),
])
async def test_remote_market_handoff_survives_plugin_proxy_headers(monkeypatch, method, path, include_origin):
    """Exercise both Uvicorn hops with a public cookie and external XFF."""
    import time
    from utils.instance_access import COOKIE, InstanceAccessMiddleware, _signed

    key = "market-remote-instance-key-" + "x" * 40
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", key)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.delenv("NEKO_INSTANCE_PUBLIC_ORIGIN", raising=False)
    client_class = httpx.AsyncClient
    plugin = FastAPI()

    @plugin.post("/market/oauth/start")
    @plugin.get("/market/ordinary")
    async def market(request: Request):
        from plugin.server.routes.market_bridge import _oauth_redirect_uri_for_request

        return {"peer": request.client.host, "authorization": request.headers.get("authorization"),
                "origin": request.headers.get("origin"),
                "xff": request.headers.get("x-forwarded-for"),
                "identity": request.scope.get("neko.instance_identity"),
                "redirect_uri": _oauth_redirect_uri_for_request(request)}

    @plugin.get("/market/bridge-token")
    async def bridge_token(request: Request):
        from plugin.server.routes.market_bridge import _require_local_bridge_token_access

        _require_local_bridge_token_access(request)
        pytest.fail("Remote handoff must not read native bridge token")

    plugin.add_middleware(InstanceAccessMiddleware)
    plugin_hop = ProxyHeadersMiddleware(plugin, trusted_hosts="127.0.0.1,::1")

    def upstream_client(**kwargs):
        return client_class(transport=httpx.ASGITransport(app=plugin_hop, client=("127.0.0.1", 50000)),
                            **kwargs)

    monkeypatch.setattr(web_app.httpx, "AsyncClient", upstream_client)
    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    main = FastAPI()
    main.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["GET", "POST"])
    main.add_middleware(InstanceAccessMiddleware)
    main_hop = ProxyHeadersMiddleware(main, trusted_hosts="127.0.0.1,::1")
    cookie = _signed(key, "session", "public.example", "owner-session", int(time.time()) + 600)
    async with client_class(transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 40000)),
                            base_url="https://public.example") as client:
        headers = {
            "Cookie": COOKIE + "=" + cookie, "Authorization": "Bearer market-oauth-token",
            "Sec-Fetch-Site": "same-origin",
            "X-Forwarded-For": "203.0.113.20", "X-Real-IP": "203.0.113.20",
            "Forwarded": "for=203.0.113.20", "X-Forwarded-Proto": "https",
            "X-Neko-Market-Internal": "caller-forged-proof",
            "X-Neko-Market-Public-Origin": "https://attacker.example",
            "X-Neko-Market-Remote": "0",
        }
        if include_origin:
            headers["Origin"] = "https://public.example"
        response = await client.request(method, path, headers=headers, content=b"{}")
    if path == "/market/bridge-token":
        assert response.status_code == 403
        return
    assert response.status_code == 200
    assert response.json() == {"peer": "127.0.0.1", "authorization": "Bearer market-oauth-token",
                               "origin": "https://public.example" if include_origin else None,
                               "xff": None, "identity": "market",
                               "redirect_uri": "https://public.example/market/oauth/callback"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("read-only"), ValueError("short key"), FileLockTimeout("key.lock")])
async def test_market_key_failure_is_503_after_body_read(monkeypatch, failure):
    import utils.instance_access as access

    body_read = []
    app = FastAPI()

    @app.middleware("http")
    async def authorized(request, call_next):
        request.scope["neko.instance_identity"] = "fixture-owner"
        return await call_next(request)

    app.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["POST"])

    async def upload():
        body_read.append(True)
        yield b"body"

    def failed_key():
        assert body_read, "Sign only after the upload finishes"
        raise failure

    monkeypatch.setattr(access, "instance_key", failed_key)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://public.example") as client:
        response = await client.post("/market/test", content=upload())
    assert response.status_code == 503
    assert response.json() == {"detail": "instance_access_unavailable"}



_RealAsyncClient = httpx.AsyncClient


def _knowledge_proxy_app(monkeypatch, seen):
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, content, headers):
            streamed = hasattr(content, "__aiter__")
            if streamed:  # a streamed body, as httpx would send it
                content = b"".join([chunk async for chunk in content])
            seen.append((method, url, content, streamed))
            return httpx.Response(200, content=b'{"ok": true}', headers={"content-type": "application/json"})

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    app = FastAPI()
    app.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["GET", "POST"])
    return app


@pytest.mark.asyncio
async def test_knowledge_uploads_are_capped_while_streamed(monkeypatch):
    from utils.http import knowledge_proxy

    seen: list = []
    app = _knowledge_proxy_app(monkeypatch, seen)
    monkeypatch.setitem(knowledge_proxy.WRITE_PATHS, "packs/import", 32)

    async def chunks():
        for _ in range(8):  # no Content-Length: only the stream itself can stop it
            yield b"x" * 16

    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        response = await client.post("/market/knowledge/packs/import?token=t", content=chunks())
        # Declared too large: refused from the header alone, before reading
        # (the 8 bytes actually sent would pass the read-time cap).
        declared = await client.post(
            "/market/knowledge/packs/import?token=t", content=b"x" * 8, headers={"content-length": "64"}
        )
    assert response.status_code == 413 and declared.status_code == 413
    assert response.json()["reason"] == "payload_too_large"
    assert seen == []  # never delivered upstream


@pytest.mark.asyncio
async def test_knowledge_bodies_are_streamed_not_buffered(monkeypatch):
    seen: list = []
    app = _knowledge_proxy_app(monkeypatch, seen)
    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        response = await client.post("/market/knowledge/packs/import?token=t", content=b'{"pack": 1}')
        read = await client.get("/market/knowledge/status?token=t")
    assert response.status_code == 200 and read.status_code == 200
    assert seen[0] == ("POST", "http://127.0.0.1:48916/market/knowledge/packs/import?token=t", b'{"pack": 1}', True)
    # A read is sent without a body, not with an empty chunked one.
    assert seen[1][0] == "GET" and seen[1][2:] == (b"", False)


@pytest.mark.asyncio
async def test_other_market_paths_are_read_as_before(monkeypatch):
    from utils.http import knowledge_proxy

    seen: list = []
    app = _knowledge_proxy_app(monkeypatch, seen)
    big = b"y" * (knowledge_proxy.PACK_BODY_MAX_BYTES + 10)
    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        response = await client.post("/market/ordinary?token=t", content=big)
    assert response.status_code == 200
    assert seen[0][2] == big



@pytest.mark.asyncio
async def test_the_bare_knowledge_path_is_capped_too(monkeypatch):
    seen: list = []
    app = _knowledge_proxy_app(monkeypatch, seen)

    async def chunks():
        for _ in range(8):
            yield b"x" * (16 * 1024)  # 128 KiB > the 64 KiB JSON cap

    async with _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        response = await client.post("/market/knowledge?token=t", content=chunks())
    assert response.status_code == 413
    assert seen == []


@pytest.mark.asyncio
async def test_remote_market_pairing_through_main_keeps_plugin_cors_headers(monkeypatch):
    """A remote Market page reaches the plugin server via Main's port.

    ``/market/pair-code`` returns Main's port (like bridge.json), so the page's
    CORS preflight and ``/token-exchange`` go through Main's ``/market`` proxy.
    Only the plugin server configures CORS for Market origins; the proxy must
    hand its CORS answer back unchanged.
    """
    from plugin.server.http_app import build_plugin_server_app
    from plugin.server.routes import market_bridge
    from plugin.settings import MARKET_ORIGINS

    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    monkeypatch.delenv("NEKO_ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.delenv("ACTIVITY_TRACKER_REMOTE", raising=False)
    monkeypatch.setattr(market_bridge, "_main_server_port", lambda: 48911)
    market_origin = MARKET_ORIGINS[0]
    real_client = httpx.AsyncClient
    plugin_app = build_plugin_server_app()

    def plugin_client(**_kwargs):
        return real_client(transport=httpx.ASGITransport(app=plugin_app, client=("127.0.0.1", 50001)))

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", plugin_client)
    main_app = FastAPI()
    main_app.add_api_route(
        "/market/{path:path}",
        web_app.proxy_user_plugin_market_bridge,
        methods=["GET", "POST", "OPTIONS"],
    )
    async with real_client(
        transport=httpx.ASGITransport(app=main_app, client=("127.0.0.1", 50002)),
        base_url="http://127.0.0.1:48911",
    ) as main:
        # The local manager (served by Main) asks for a pairing code.
        pairing = await main.post("/market/pair-code", headers={"Origin": "http://127.0.0.1:48911"})
        assert pairing.status_code == 200, pairing.text
        assert pairing.json()["port"] == 48911

        preflight = await main.options(
            "/market/token-exchange",
            headers={
                "Origin": market_origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == market_origin

        exchanged = await main.post(
            "/market/token-exchange",
            headers={"Origin": market_origin},
            json={"one_time_code": pairing.json()["one_time_code"]},
        )
        assert exchanged.status_code == 200
        assert exchanged.headers["access-control-allow-origin"] == market_origin
        assert exchanged.json()["bridge_token"] == market_bridge.get_bridge_token()

        # The remote page itself can never mint a code.
        refused = await main.post("/market/pair-code", headers={"Origin": market_origin})
        assert refused.status_code == 403


@pytest.mark.asyncio
async def test_market_proxy_strips_client_internal_service_header(monkeypatch):
    """proxy_user_plugin_market_bridge must strip any client-supplied X-Neko-Internal-Service header."""
    seen_headers: dict[str, str] = {}
    asgi_client = httpx.AsyncClient

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, _method, _url, *, content, headers):
            seen_headers.update(headers)
            return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48916")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    app = FastAPI()
    app.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["POST"])
    async with asgi_client(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        resp = await client.post(
            "/market/ordinary",
            headers={
                "X-Neko-Internal-Service": "attacker-forged-proof",
                "X-Neko-Market-Internal": "attacker-market-proof",
                "Origin": "http://localhost:48911",
            },
            content=b"{}",
        )

    assert resp.status_code == 200
    lowered = {k.lower(): v for k, v in seen_headers.items()}
    assert "x-neko-internal-service" not in lowered
    assert "x-neko-market-internal" not in lowered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode_env",
    ["NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE"],
    ids=["behind-proxy", "activity-tracker-remote"],
)
async def test_market_proxy_preserves_internal_service_installer_auth_and_blocks_browser_escalation(
    monkeypatch,
    mode_env: str,
):
    """Main /market proxy must re-sign internal-service installer calls across hops while keeping browser sessions confined to market remote identity."""
    import time
    import config as cfg
    from utils.http.internal_client import internal_service_event_hooks
    from utils.instance_access import COOKIE, INTERNAL_SERVICE_HEADER, InstanceAccessMiddleware, _signed

    key = "market-installer-internal-key-" + "y" * 40
    for env_name in ("NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", key)
    monkeypatch.setenv(mode_env, "true")

    main_port = int(cfg.MAIN_SERVER_PORT)
    plugin_port = int(cfg.USER_PLUGIN_SERVER_PORT)
    monkeypatch.setattr(cfg, "MAIN_SERVER_PORT", main_port, raising=False)
    monkeypatch.setattr(cfg, "USER_PLUGIN_SERVER_PORT", plugin_port, raising=False)
    monkeypatch.setattr(cfg, "USER_PLUGIN_BASE", f"http://127.0.0.1:{plugin_port}", raising=False)

    real_client = httpx.AsyncClient
    plugin = FastAPI()

    @plugin.post("/market/install")
    @plugin.get("/market/tasks/{task_id}")
    async def market_install_or_task(request: Request, task_id: str | None = None, token: str = ""):
        lowered = {k.lower() for k in request.headers.keys()}
        return {
            "identity": request.scope.get("neko.instance_identity"),
            "market_remote": bool(request.scope.get("neko.market_remote_authorized")),
            "has_internal_header": INTERNAL_SERVICE_HEADER in lowered,
            "has_market_internal_header": "x-neko-market-internal" in lowered,
            "token": token,
            "task_id": task_id,
        }

    @plugin.get("/market/bridge-token")
    async def market_bridge_token(request: Request):
        from plugin.server.routes.market_bridge import _require_local_bridge_token_access

        _require_local_bridge_token_access(request)
        return {"bridge_token": "secret"}

    plugin.add_middleware(InstanceAccessMiddleware)
    plugin_hop = ProxyHeadersMiddleware(plugin, trusted_hosts="127.0.0.1,::1")

    def upstream_client(**_kwargs):
        return real_client(
            transport=httpx.ASGITransport(app=plugin_hop, client=("127.0.0.1", 50000)),
        )

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: f"http://127.0.0.1:{plugin_port}")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", upstream_client)

    main = FastAPI()
    main.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["GET", "POST"])
    main.add_middleware(InstanceAccessMiddleware)
    main_hop = ProxyHeadersMiddleware(main, trusted_hosts="127.0.0.1,::1")

    # 1. Internal installer hop with internal_service_event_hooks() succeeds on both POST and GET.
    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 50001)),
        base_url=f"http://127.0.0.1:{main_port}",
        event_hooks=internal_service_event_hooks(),
    ) as installer_client:
        install_resp = await installer_client.post(
            f"http://127.0.0.1:{main_port}/market/install",
            params={"token": "bridge-tok"},
            json={"plugin_id": "demo"},
        )
        assert install_resp.status_code == 200
        assert install_resp.json() == {
            "identity": "internal",
            "market_remote": False,
            "has_internal_header": True,
            "has_market_internal_header": False,
            "token": "bridge-tok",
            "task_id": None,
        }

        poll_resp = await installer_client.get(
            f"http://127.0.0.1:{main_port}/market/tasks/task-1",
            params={"token": "bridge-tok"},
        )
        assert poll_resp.status_code == 200
        assert poll_resp.json() == {
            "identity": "internal",
            "market_remote": False,
            "has_internal_header": True,
            "has_market_internal_header": False,
            "token": "bridge-tok",
            "task_id": "task-1",
        }

    # 2. Unsigned loopback caller without internal proof is rejected with 401.
    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 50002)),
        base_url=f"http://127.0.0.1:{main_port}",
    ) as unsigned_client:
        denied = await unsigned_client.post("/market/install?token=bridge-tok", json={})
        assert denied.status_code == 401

    # 3. Remote browser session cookie + forged X-Neko-Internal-Service cannot escalate to internal identity.
    cookie = _signed(key, "session", "public.example", "owner-session", int(time.time()) + 600)
    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 40000)),
        base_url="https://public.example",
    ) as browser_client:
        browser_headers = {
            "Cookie": f"{COOKIE}={cookie}",
            "Origin": "https://public.example",
            "Sec-Fetch-Site": "same-origin",
            "X-Neko-Internal-Service": "attacker-forged-proof",
        }
        bridge_denied = await browser_client.get("/market/bridge-token", headers=browser_headers)
        assert bridge_denied.status_code == 403

        install_browser = await browser_client.post(
            "/market/install?token=bridge-tok",
            headers=browser_headers,
            json={"plugin_id": "demo"},
        )
        assert install_browser.status_code == 200
        assert install_browser.json() == {
            "identity": "market",
            "market_remote": True,
            "has_internal_header": False,
            "has_market_internal_header": True,
            "token": "bridge-tok",
            "task_id": None,
        }


@pytest.mark.asyncio
async def test_market_proxy_slow_internal_upload_survives_proof_expiry_and_revokes_on_key_rotation(monkeypatch):
    """Slow internal uploads must survive 60s inbound proof expiry while key rotation or missing admission grant fails closed."""
    import time
    from starlette.requests import ClientDisconnect
    import config as cfg
    import utils.instance_access as access
    from tests.fake_clock import patch_module_clock
    from utils.http.internal_client import internal_service_event_hooks
    from utils.instance_access import INTERNAL_SERVICE_HEADER, InstanceAccessMiddleware

    key1 = "market-slow-upload-key-one-" + "a" * 40
    key2 = "market-slow-upload-key-two-" + "b" * 40
    current_key = [key1]
    now = [time.time()]
    mono = [1000.0]

    for env_name in ("NEKO_BEHIND_PROXY", "NEKO_ACTIVITY_TRACKER_REMOTE", "ACTIVITY_TRACKER_REMOTE", "NEKO_INSTANCE_ACCESS_KEY"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    monkeypatch.setattr(access, "instance_key", lambda: current_key[0])
    patch_module_clock(monkeypatch, access, time=lambda: now[0], monotonic=lambda: mono[0])

    main_port = int(cfg.MAIN_SERVER_PORT)
    plugin_port = int(cfg.USER_PLUGIN_SERVER_PORT)
    monkeypatch.setattr(cfg, "MAIN_SERVER_PORT", main_port, raising=False)
    monkeypatch.setattr(cfg, "USER_PLUGIN_SERVER_PORT", plugin_port, raising=False)
    monkeypatch.setattr(cfg, "USER_PLUGIN_BASE", f"http://127.0.0.1:{plugin_port}", raising=False)

    real_client = httpx.AsyncClient
    plugin_hits: list[str] = []
    plugin = FastAPI()

    @plugin.post("/market/install")
    async def market_install(request: Request):
        plugin_hits.append(request.url.path)
        lowered = {k.lower() for k in request.headers.keys()}
        return {
            "identity": request.scope.get("neko.instance_identity"),
            "market_remote": bool(request.scope.get("neko.market_remote_authorized")),
            "has_internal_header": INTERNAL_SERVICE_HEADER in lowered,
            "body_size": len(await request.body()),
        }

    plugin.add_middleware(InstanceAccessMiddleware)
    plugin_hop = ProxyHeadersMiddleware(plugin, trusted_hosts="127.0.0.1,::1")

    def upstream_client(**_kwargs):
        return real_client(
            transport=httpx.ASGITransport(app=plugin_hop, client=("127.0.0.1", 50000)),
        )

    monkeypatch.setattr(web_app, "_resolve_user_plugin_base", lambda: f"http://127.0.0.1:{plugin_port}")
    monkeypatch.setattr(web_app.httpx, "AsyncClient", upstream_client)

    observed_digests: list[object] = []
    main = FastAPI()

    @main.middleware("http")
    async def capture_digest(request: Request, call_next):
        if "neko.internal_service_key_digest" in request.scope:
            observed_digests.append(request.scope["neko.internal_service_key_digest"])
        return await call_next(request)

    main.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["POST"])
    main.add_middleware(InstanceAccessMiddleware)
    main_hop = ProxyHeadersMiddleware(main, trusted_hosts="127.0.0.1,::1")

    # 1. Slow upload exceeding 60s inbound proof TTL and 1s monotonic recheck interval still succeeds.
    async def slow_upload():
        yield b'{"plugin_id":'
        now[0] += 120.0
        mono[0] += 120.0
        yield b'"demo"}'

    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 50001)),
        base_url=f"http://127.0.0.1:{main_port}",
        event_hooks=internal_service_event_hooks(),
    ) as installer_client:
        slow_resp = await installer_client.post(
            f"http://127.0.0.1:{main_port}/market/install",
            content=slow_upload(),
            headers={"content-type": "application/json"},
        )
    assert slow_resp.status_code == 200
    assert slow_resp.json() == {
        "identity": "internal",
        "market_remote": False,
        "has_internal_header": True,
        "body_size": len(b'{"plugin_id":"demo"}'),
    }
    assert plugin_hits == ["/market/install"]
    assert len(observed_digests) == 1
    assert isinstance(observed_digests[0], bytes)
    assert len(observed_digests[0]) == 32
    assert key1.encode("utf-8") not in observed_digests[0]

    # 2a. Rotating the instance key before the monotonic recheck deadline fails at proxy digest check with 401.
    plugin_hits.clear()

    async def pre_deadline_rotated_upload():
        yield b'{"plugin_id":'
        current_key[0] = key2
        yield b'"demo"}'

    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 50002)),
        base_url=f"http://127.0.0.1:{main_port}",
        event_hooks=internal_service_event_hooks(),
    ) as installer_client:
        rotated_resp = await installer_client.post(
            f"http://127.0.0.1:{main_port}/market/install",
            content=pre_deadline_rotated_upload(),
            headers={"content-type": "application/json"},
        )
    assert rotated_resp.status_code == 401
    assert rotated_resp.json() == {"detail": "instance_authorization_required"}
    assert plugin_hits == []

    # 2b. Rotating the instance key after the monotonic recheck deadline aborts body read via http.disconnect.
    plugin_hits.clear()
    current_key[0] = key1

    async def post_deadline_rotated_upload():
        yield b'{"plugin_id":'
        current_key[0] = key2
        mono[0] += 2.0
        yield b'"demo"}'

    async with real_client(
        transport=httpx.ASGITransport(app=main_hop, client=("127.0.0.1", 50003)),
        base_url=f"http://127.0.0.1:{main_port}",
        event_hooks=internal_service_event_hooks(),
    ) as installer_client:
        with pytest.raises(ClientDisconnect):
            await installer_client.post(
                f"http://127.0.0.1:{main_port}/market/install",
                content=post_deadline_rotated_upload(),
                headers={"content-type": "application/json"},
            )
    assert plugin_hits == []

    # 3. Spoofed scope identity without a matching middleware digest fails closed.
    plugin_hits.clear()
    current_key[0] = key1
    for extra_digest in (None, b"wrong-digest", "wrong-type"):
        spoofed_app = FastAPI()

        @spoofed_app.middleware("http")
        async def inject_only_identity(request, call_next, digest=extra_digest):
            request.scope["neko.instance_identity"] = "internal"
            if digest is not None:
                request.scope["neko.internal_service_key_digest"] = digest
            return await call_next(request)

        spoofed_app.add_api_route("/market/{path:path}", web_app.proxy_user_plugin_market_bridge, methods=["POST"])
        async with real_client(
            transport=httpx.ASGITransport(app=spoofed_app, client=("127.0.0.1", 50004)),
            base_url=f"http://127.0.0.1:{main_port}",
        ) as spoofed_client:
            spoofed_resp = await spoofed_client.post("/market/install", json={"plugin_id": "demo"})
        assert spoofed_resp.status_code == 401
        assert spoofed_resp.json() == {"detail": "instance_authorization_required"}
        assert plugin_hits == []

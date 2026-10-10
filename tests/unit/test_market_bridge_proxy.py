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

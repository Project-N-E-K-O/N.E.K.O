"""Bridge tokens remain restricted to native local calls across proxy layers."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from plugin.server.routes.market_bridge import _require_local_bridge_token_access
from plugin.server.infrastructure.development_access import require_development_access


pytestmark = pytest.mark.plugin_unit


@pytest.mark.parametrize("forwarded", [False, True])
def test_loopback_proxy_cannot_use_forged_local_host_to_read_bridge_token(forwarded):
    app = FastAPI()
    token_reads = []

    @app.get("/token")
    async def token(request: Request):
        _require_local_bridge_token_access(request)
        token_reads.append(True)
        return {"token": "test-secret"}

    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1,::1"),
                    base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        headers = {"X-Forwarded-For": "127.0.0.1"} if forwarded else {}
        response = client.get("/token", headers=headers)
    assert response.status_code == (403 if forwarded else 200)
    assert len(token_reads) == (0 if forwarded else 1)


@pytest.mark.parametrize("forwarded", [False, True])
def test_development_access_rejects_proxy_rewritten_local_identity(forwarded):
    app = FastAPI()

    @app.get("/development")
    async def development(request: Request):
        require_development_access(request)
        return {"ok": True}

    with TestClient(ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1,::1"),
                    base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        headers = {"X-Neko-Development": "1"}
        if forwarded:
            headers["X-Forwarded-For"] = "127.0.0.1"
        response = client.get("/development", headers=headers)
    assert response.status_code == (403 if forwarded else 200)

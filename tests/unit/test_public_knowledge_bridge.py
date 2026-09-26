from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from types import SimpleNamespace


def _client(monkeypatch, captured: dict) -> TestClient:
    from plugin.server.routes import market_bridge as module

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, target, **kwargs):
            captured.update(method=method, target=target, **kwargs)
            return httpx.Response(
                200,
                content=b'{"ok":true}',
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(module, "_verify_token", lambda _token: None)
    monkeypatch.setattr(module, "_require_local_bridge_token_access", lambda _request: 48910)
    monkeypatch.setattr(module, "_main_server_port", lambda: 48911)
    def fake_client(**kwargs):
        captured["client_options"] = kwargs
        return FakeClient()

    monkeypatch.setattr(module.httpx, "AsyncClient", fake_client)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def test_knowledge_bridge_forwards_only_allowlisted_local_api(monkeypatch):
    captured = {}
    client = _client(monkeypatch, captured)

    response = client.post(
        "/market/knowledge/packs/import",
        params={"token": "fixture"},
        json={"pack": {"schema_version": 1}},
    )

    assert response.json() == {"ok": True}
    assert captured["target"] == (
        "http://127.0.0.1:48911/api/public-knowledge/packs/import"
    )
    assert ("token", "fixture") not in captured["params"]
    assert captured["headers"]["Origin"] == "http://127.0.0.1:48911"
    assert captured["headers"]["X-CSRF-Token"]
    assert captured["client_options"]["timeout"].read == 40


def test_knowledge_bridge_forwards_degraded_job_discard(monkeypatch):
    captured = {}
    client = _client(monkeypatch, captured)

    response = client.post(
        "/market/knowledge/packs/jobs/discard",
        params={"token": "fixture"},
        json={"job_id": "degraded-fixture"},
    )

    assert response.json() == {"ok": True}
    assert captured["target"] == (
        "http://127.0.0.1:48911/api/public-knowledge/packs/jobs/discard"
    )


def test_knowledge_bridge_rejects_arbitrary_proxy_paths(monkeypatch):
    captured = {}
    client = _client(monkeypatch, captured)

    response = client.get(
        "/market/knowledge/system/config",
        params={"token": "fixture"},
    )

    assert response.status_code == 404
    assert captured == {}


def test_browser_bridge_cannot_apply_trusted_market_subscription(monkeypatch):
    captured = {}
    client = _client(monkeypatch, captured)

    response = client.post(
        "/market/knowledge/subscriptions/apply",
        params={"token": "fixture"},
        content=b"must-not-be-read-or-forwarded",
        headers={"content-type": "multipart/form-data; boundary=fixture"},
    )

    assert response.status_code == 404
    assert captured == {}


def test_knowledge_bridge_keeps_get_timeout_short(monkeypatch):
    from plugin.server.routes import market_bridge as module

    captured = {}
    client = _client(monkeypatch, captured)

    response = client.get(
        "/market/knowledge/status",
        params={"token": "fixture"},
    )

    assert response.status_code == 200
    assert captured["client_options"]["timeout"].read == (
        module.KNOWLEDGE_GET_TIMEOUT_SECONDS
    )


def test_local_bridge_accepts_only_supported_local_origins(monkeypatch):
    from plugin.server.routes import market_bridge as module

    monkeypatch.setattr(module, "_main_server_port", lambda: 49321)

    def request(origin: str):
        return SimpleNamespace(
            headers={"host": "127.0.0.1:48910", "origin": origin},
            client=SimpleNamespace(host="::1"),
        )

    assert module._require_local_bridge_token_access(
        request("http://localhost:48910")
    ) == 48910
    assert module._require_local_bridge_token_access(
        request("http://127.0.0.1:49321")
    ) == 48910
    assert module._require_local_bridge_token_access(
        request("http://localhost:5173")
    ) == 48910
    assert module._require_local_bridge_token_access(
        request("http://127.0.0.1:5173")
    ) == 48910

    denied_origins = (
        "http://localhost:49322",
        "https://localhost:49321",
        "http://user@localhost:49321",
        "http://localhost:49321/manager",
        "http://localhost:49321?view=knowledge",
        "http://market.example:49321",
        "http://localhost:not-a-port",
    )
    for origin in denied_origins:
        with pytest.raises(HTTPException) as failure:
            module._require_local_bridge_token_access(request(origin))
        assert failure.value.status_code == 403


def test_knowledge_bridge_rejects_oversized_body_before_forwarding(monkeypatch):
    captured = {}
    client = _client(monkeypatch, captured)

    response = client.post(
        "/market/knowledge/entry/disabled",
        params={"token": "fixture"},
        content=b"x" * (64 * 1024 + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "knowledge_request_too_large"
    assert captured == {}


def test_knowledge_management_bridge_rejects_remote_market_origin(monkeypatch):
    from plugin.server.routes import market_bridge as module

    captured = {}
    client = _client(monkeypatch, captured)
    monkeypatch.setattr(
        module,
        "_require_local_bridge_token_access",
        lambda _request: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail="仅允许本地同源访问")
        ),
    )

    response = client.post(
        "/market/knowledge/packs/remove",
        params={"token": "paired-market-token"},
        json={"pack_id": "fixture"},
        headers={"origin": "https://market.example"},
    )

    assert response.status_code == 403
    assert captured == {}


def test_knowledge_timeouts_distinguish_mutation_from_read_without_retry(monkeypatch):
    from plugin.server.routes import market_bridge as module

    attempts = 0

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("fixture timeout")

    monkeypatch.setattr(module, "_verify_token", lambda _token: None)
    monkeypatch.setattr(module, "_require_local_bridge_token_access", lambda _request: 48910)
    monkeypatch.setattr(module, "_main_server_port", lambda: 48911)
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: TimeoutClient())
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).post(
        "/market/knowledge/packs/remove",
        params={"token": "fixture"},
        json={"pack_id": "fixture"},
    )

    assert response.status_code == 504
    assert response.json() == {"detail": {"code": "knowledge_mutation_timeout"}}
    read_response = TestClient(app).get(
        "/market/knowledge/status",
        params={"token": "fixture"},
    )
    assert read_response.status_code == 504
    assert read_response.json() == {"detail": {"code": "knowledge_request_timeout"}}
    assert attempts == 2


@pytest.mark.asyncio
async def test_packaged_proxy_uses_knowledge_budgets_and_stable_timeout(monkeypatch):
    from app.main_server import web_app as module
    from knowledge.timeouts import (
        KNOWLEDGE_BROWSER_MUTATION_TIMEOUT_SECONDS,
        KNOWLEDGE_GET_TIMEOUT_SECONDS,
        KNOWLEDGE_MAIN_TO_PLUGIN_MUTATION_TIMEOUT_SECONDS,
        KNOWLEDGE_PLUGIN_TO_MAIN_MUTATION_TIMEOUT_SECONDS,
    )
    from knowledge._mutation_lock import MUTATION_LOCK_TIMEOUT_SECONDS

    captured_timeouts: list[float] = []
    attempts = 0

    class FakeClient:
        def __init__(self, *, timeout, **_kwargs):
            captured_timeouts.append(timeout.read)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if captured_timeouts[-1] == KNOWLEDGE_MAIN_TO_PLUGIN_MUTATION_TIMEOUT_SECONDS:
                raise httpx.ReadTimeout("fixture timeout")
            return httpx.Response(
                200,
                content=b'{"ok":true}',
                headers={"content-type": "application/json"},
            )

    async def body():
        return b"{}"

    def request(method: str):
        return SimpleNamespace(
            method=method,
            url=SimpleNamespace(query=""),
            headers={"content-type": "application/json"},
            body=body,
        )

    monkeypatch.setattr(module, "_resolve_user_plugin_base", lambda: "http://127.0.0.1:48910")
    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)

    get_response = await module.proxy_user_plugin_market_bridge(
        request("GET"),
        "knowledge/status",
    )
    timeout_response = await module.proxy_user_plugin_market_bridge(
        request("POST"),
        "knowledge/packs/remove",
    )
    ordinary_response = await module.proxy_user_plugin_market_bridge(
        request("GET"),
        "status",
    )

    assert get_response.status_code == 200
    assert captured_timeouts == [
        KNOWLEDGE_GET_TIMEOUT_SECONDS,
        KNOWLEDGE_MAIN_TO_PLUGIN_MUTATION_TIMEOUT_SECONDS,
        30.0,
    ]
    assert (
        MUTATION_LOCK_TIMEOUT_SECONDS
        < KNOWLEDGE_PLUGIN_TO_MAIN_MUTATION_TIMEOUT_SECONDS
        < KNOWLEDGE_MAIN_TO_PLUGIN_MUTATION_TIMEOUT_SECONDS
        < KNOWLEDGE_BROWSER_MUTATION_TIMEOUT_SECONDS
    )
    assert timeout_response.status_code == 504
    assert timeout_response.body == b'{"detail":{"code":"knowledge_mutation_timeout"}}'
    assert ordinary_response.status_code == 200
    assert attempts == 3


def test_bridge_token_error_uses_stable_code():
    from plugin.server.routes import market_bridge as module

    with pytest.raises(HTTPException) as failure:
        module._verify_token("not-the-current-token")

    assert failure.value.status_code == 403
    assert failure.value.detail == {
        "code": "invalid_bridge_token",
        "message": "无效的 bridge token",
    }

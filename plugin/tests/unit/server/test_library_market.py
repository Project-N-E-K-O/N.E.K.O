"""Credential isolation and bounded streaming for personal-library Market transport."""

import hashlib
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from plugin.server.routes import library_market as bridge


pytestmark = pytest.mark.plugin_unit


def mount(monkeypatch, handler, actor=None):
    """Keep all network requests in an explicit fake Market transport."""
    client_class = httpx.AsyncClient
    actor = actor if actor is not None else {"id": 41}
    async def remote(request):
        if request.url.path == "/api/v1/auth/me":
            return httpx.Response(200, json=actor)
        value = handler(request)
        return await value if hasattr(value, "__await__") else value
    transport = httpx.MockTransport(remote)
    monkeypatch.setattr(bridge.httpx, "AsyncClient", lambda **kwargs: client_class(transport=transport, **kwargs))
    monkeypatch.setattr(bridge, "get_market_api_url", lambda: "https://market.example.test")
    monkeypatch.setattr(bridge, "get_market_web_url", lambda: "https://market.example.test")

    async def token():
        return {"access_token": "private-server-access-token"}

    monkeypatch.setattr(bridge.account, "_authenticated_market_token", token)
    app = FastAPI()
    app.include_router(bridge.router)
    client = TestClient(app, base_url="http://127.0.0.1:48916", client=("127.0.0.1", 55000))
    headers = {"Authorization": "Bearer " + bridge.account.get_bridge_token(), "Origin": "http://127.0.0.1:48916"}
    return client, headers


def detail(raw, **changes):
    """Return the real shares API's bounded frozen-identity shape."""
    return {"id": 7, "kind": "knowledge", "title": "Public notes", "status": "published", "revision": 2,
            "byte_size": len(raw), "file_sha256": hashlib.sha256(raw).hexdigest(),
            "preview": {"format": "neko.personal-knowledge-package", "format_version": 1,
                        "package_id": "work", "package_version": "1"}, **changes}


def test_unpaired_and_remote_pages_cannot_read_or_publish_as_desktop(monkeypatch):
    seen = []
    client, headers = mount(monkeypatch, lambda request: seen.append(request) or httpx.Response(200, json={}))
    assert client.post("/market/library/publish", content=b"private file").status_code == 403
    hostile = {**headers, "Origin": "https://market.example.test"}
    assert client.post("/market/library/publish", headers=hostile, content=b"private file").status_code == 403
    assert seen == []


@pytest.mark.parametrize("description", ["", "中文资料介绍" * 800])
def test_publish_streams_real_multipart_and_keeps_oauth_token_server_side(monkeypatch, description):
    raw = b"tiny controlled package bytes"
    seen = []

    async def remote(request):
        seen.append((request, await request.aread()))
        return httpx.Response(201, json=detail(raw, status="pending_review"))

    client, headers = mount(monkeypatch, remote)
    metadata = {"artifact_id": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw),
                "title": "Public notes", "description": description, "client_request_id": str(uuid4()),
                "publisher_id": 41, "market_api_url": "https://market.example.test"}
    response = client.post("/market/library/publish", headers=headers,
                           content=json.dumps(metadata, ensure_ascii=False).encode() + b"\n" + raw)
    assert response.status_code == 200
    assert response.json()["share"]["status"] == "pending_review"
    request, body = seen[0]
    assert str(request.url) == "https://market.example.test/api/v1/shares"
    assert request.headers["authorization"] == "Bearer private-server-access-token"
    assert raw in body and metadata["client_request_id"].encode() in body
    assert int(request.headers["content-length"]) == len(body)
    assert "private-server-access-token" not in response.text
    assert bridge.account.get_bridge_token() not in response.text


def test_publish_refuses_changed_account_before_uploading_any_private_bytes(monkeypatch):
    """The actor check uses the exact token later used for the shares request."""
    raw, uploads = b"private notes", []
    actor = {"id": 41}
    client, headers = mount(monkeypatch, lambda request: uploads.append(request) or httpx.Response(500), actor)
    publisher = client.get("/market/library/publisher", headers=headers)
    assert publisher.status_code == 200
    assert publisher.json()["publisher_id"] == 41
    actor["id"] = 42
    metadata = {"artifact_id": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw), "title": "Notes",
                "client_request_id": str(uuid4()), "publisher_id": 41, "market_api_url": "https://market.example.test"}
    response = client.post("/market/library/publish", headers=headers,
                           content=json.dumps(metadata).encode() + b"\n" + raw)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_publisher_changed"
    assert uploads == []


def test_refreshed_other_account_cannot_replay_uncertain_publication(monkeypatch):
    """A rejected token may refresh, but a replay retains the original author."""
    raw, uploads = b"private notes", []
    actor = {"id": 41}
    def remote(request):
        uploads.append(request.headers["authorization"])
        return httpx.Response(401, json={"detail": {"code": "token_expired"}})
    client, headers = mount(monkeypatch, remote, actor)
    async def current_token():
        return {"access_token": "private-server-access-token" if actor["id"] == 41 else "different-account-token"}
    monkeypatch.setattr(bridge.account, "_authenticated_market_token", current_token)
    async def refresh(**_kwargs):
        actor["id"] = 42
        return {"access_token": "different-account-token"}
    monkeypatch.setattr(bridge.account, "_ensure_valid_oauth_token", refresh)
    metadata = {"artifact_id": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw), "title": "Notes",
                "client_request_id": str(uuid4()), "publisher_id": 41, "market_api_url": "https://market.example.test"}
    body = json.dumps(metadata).encode() + b"\n" + raw
    first = client.post("/market/library/publish", headers=headers, content=body)
    second = client.post("/market/library/publish", headers=headers, content=body)
    assert first.status_code == 409 and first.json()["detail"]["code"] == "market_auth_retry"
    assert second.status_code == 409 and second.json()["detail"]["code"] == "market_publisher_changed"
    assert uploads == ["Bearer private-server-access-token"]


def test_download_rejects_stale_revision_before_retrieving_file(monkeypatch):
    raw = b"package data"
    seen = []

    def remote(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=detail(raw))

    client, headers = mount(monkeypatch, remote)
    response = client.get("/market/library/shares/7/download", headers=headers,
                          params={"revision": 1, "file_sha256": hashlib.sha256(raw).hexdigest()})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "market_revision_changed"
    assert seen == ["/api/v1/shares/7"]


def test_download_streams_only_exact_published_artifact(monkeypatch):
    raw = b"package data" * 100

    def remote(request):
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=raw)
        return httpx.Response(200, json=detail(raw))

    client, headers = mount(monkeypatch, remote)
    response = client.get("/market/library/shares/7/download", headers=headers,
                          params={"revision": 2, "file_sha256": hashlib.sha256(raw).hexdigest()})
    assert response.status_code == 200
    assert response.content == raw
    assert response.headers["content-length"] == str(len(raw))


def test_non_package_or_nonpublished_share_never_reaches_download(monkeypatch):
    seen = []

    def remote(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=detail(b"old", status="withdrawn"))

    client, headers = mount(monkeypatch, remote)
    response = client.get("/market/library/shares/7/download", headers=headers,
                          params={"revision": 2, "file_sha256": hashlib.sha256(b"old").hexdigest()})
    assert response.status_code == 409
    assert seen == ["/api/v1/shares/7"]

"""Publish only explicitly confirmed, identity-checked unified artifacts."""

import hashlib
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from plugin.server.routes import knowledge_publish as publish
from tests.unit.test_knowledge_archive_v2 import make_archive


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    """Keep real streaming transports and package checks with synthetic account data."""
    raw = make_archive(tmp_path / "source.zip").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    events = []
    original_client = httpx.AsyncClient

    async def market_token():
        events.append("account")
        return {"access_token": "isolated-test-bearer"}

    async def upstream(request):
        if request.url.host == "127.0.0.1":
            assert "authorization" not in request.headers
            assert request.url.params["artifact_id"] == digest
            events.append("artifact")
            return httpx.Response(200, content=raw)
        assert request.url.host == "market.test"
        assert request.headers["authorization"] == "Bearer isolated-test-bearer"
        body = await request.aread()
        assert b".neko-knowledge.zip" in body and raw in body
        assert b"client_request_id" in body
        events.append("publication")
        return httpx.Response(201, json={"id": 7, "kind": "knowledge", "status": "pending_review",
                                         "file_sha256": digest, "revision": 1})

    app = FastAPI()
    app.include_router(publish.router)
    client = original_client(transport=httpx.ASGITransport(app=app), base_url="http://local.test")
    monkeypatch.setattr(publish.account, "_require_local_bridge_token_access", lambda _request: None)
    monkeypatch.setattr(publish.account, "_verify_token", lambda _token: None)
    monkeypatch.setattr(publish.account, "_authenticated_market_token", market_token)
    monkeypatch.setattr(publish.account, "_main_server_port", lambda: 48911)
    monkeypatch.setattr(publish, "get_market_api_url", lambda: "https://market.test")
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(upstream), **kwargs))
    data = {"pack_id": "imp.example", "artifact_id": digest, "publish_confirmed": True,
            "client_request_id": str(uuid4()), "title": "Shared sample", "description": "Synthetic only"}
    async with client:
        yield client, data, events


async def test_publication_streams_the_confirmed_artifact_to_existing_shares(setup):
    client, data, events = setup
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["share"]["id"] == 7
    assert events == ["account", "artifact", "publication"]
    assert "bearer" not in response.text


async def test_publication_confirmation_precedes_account_or_artifact_reads(setup):
    client, data, events = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "publish_confirmed": False})
    assert response.status_code == 400
    assert response.json()["reason"] == "market_publication_not_confirmed"
    assert events == []


async def test_a_different_pack_artifact_is_rejected_before_publication(setup):
    client, data, events = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "pack_id": "imp.other"})
    assert response.status_code == 409
    assert response.json()["reason"] == "market_identity_mismatch"
    assert "publication" not in events

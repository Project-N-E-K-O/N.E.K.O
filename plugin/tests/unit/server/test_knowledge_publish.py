"""Publish only explicitly confirmed, identity-checked unified artifacts."""

import hashlib
import asyncio
from email import policy
from email.parser import BytesParser
import threading
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
    reply = {"status": 201, "content": None}
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
        assert request.method == "POST" and request.url.path == "/api/v1/shares"
        assert request.headers["authorization"] == "Bearer isolated-test-bearer"
        body = await request.aread()
        assert b".neko-knowledge.zip" in body and raw in body
        message = BytesParser(policy=policy.default).parsebytes(
            b'Content-Type: ' + request.headers["content-type"].encode() + b'\r\n\r\n' + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                  for part in message.iter_parts()}
        assert fields["client_request_id"].decode() == data["client_request_id"]
        events.append("publication")
        if reply["content"] is not None:
            return httpx.Response(reply["status"], content=reply["content"])
        return httpx.Response(reply["status"], json={"id": 7, "kind": "knowledge", "status": "pending_review",
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
        yield client, data, events, reply


async def test_publication_streams_the_confirmed_artifact_to_existing_shares(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["share"]["id"] == 7
    assert events == ["account", "artifact", "publication"]
    assert "bearer" not in response.text


async def test_publication_confirmation_precedes_account_or_artifact_reads(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "publish_confirmed": False})
    assert response.status_code == 400
    assert response.json()["reason"] == "market_publication_not_confirmed"
    assert events == []


async def test_a_different_pack_artifact_is_rejected_before_publication(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "pack_id": "imp.other"})
    assert response.status_code == 409
    assert response.json()["reason"] == "market_identity_mismatch"
    assert "publication" not in events


async def test_publication_refuses_cleartext_remote_market_before_reading_artifact(setup, monkeypatch):
    client, data, events, _reply = setup
    monkeypatch.setattr(publish, "get_market_api_url", lambda: "http://market.test")
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_not_connected"
    assert "artifact" not in events


async def test_publication_scratch_creation_failure_is_a_structured_error(setup, monkeypatch):
    client, data, _events, _reply = setup
    def full_disk(**_kwargs):
        raise OSError("synthetic full scratch volume")
    monkeypatch.setattr(publish.tempfile, "mkdtemp", full_disk)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"


async def test_publication_deadline_covers_artifact_download(setup, monkeypatch):
    client, data, _events, _reply = setup
    async def held_download(*_args):
        await asyncio.sleep(30)
    monkeypatch.setattr(publish, "_download", held_download)
    monkeypatch.setattr(publish, "_PUBLICATION_TIMEOUT_SECONDS", 0.01)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"


@pytest.mark.parametrize("status,reason", [(401, "market_auth_required"), (502, "market_publication_failed")])
async def test_market_status_is_not_hidden_by_a_non_json_error_body(setup, status, reason):
    client, data, _events, reply = setup
    reply.update(status=status, content=b"<html>upstream gateway</html>")
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == status and response.json()["reason"] == reason


async def test_cancelled_publication_drains_the_validator_before_scratch_cleanup(setup, monkeypatch, tmp_path):
    client, data, _events, _reply = setup
    started, release = threading.Event(), threading.Event()
    directory = tmp_path / "publication-scratch"
    directory.mkdir()
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    original = publish.validate_package
    def held_validator(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(publish, "validate_package", held_validator)
    task = asyncio.create_task(client.post("/market/knowledge/publish?token=test", json=data))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert directory.exists() and not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not directory.exists()

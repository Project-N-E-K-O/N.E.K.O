"""Exercise real builder ZIPs through the native worker, Main and Memory.

Only the remote Market transport and encoder identity are synthetic. The
multipart forwarding, knowledge routes, import job and SQLite writes are real.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import zipfile

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService
from knowledge.subscriptions import SubscriptionError, parse_subscription
from main_routers import public_knowledge_router
from main_routers.system_router import _shared as system_shared
from plugin.server.routes import knowledge_market as km
from plugin.server.routes.market_bridge import get_bridge_token


pytestmark = pytest.mark.plugin_unit


def _descriptor(package_id=1, remote_id="knowledge/1", *, pack_id="imp.acceptance", raw=b"synthetic", version="1.0.0"):
    """Use the same numeric remote identity and frozen URL as Market shares."""
    digest = hashlib.sha256(raw).hexdigest()
    return {"protocol_version": 2, "package_id": package_id, "remote_id": remote_id,
            "pack_id": pack_id, "material_type": "knowledge", "version": version, "channel": "stable",
            "artifacts": {"knowledge": {"url": f"https://market-acceptance.invalid/api/v1/shares/{package_id}/download?revision=1&file_sha256={digest}",
                                         "sha256": digest, "bytes": len(raw)}}}


def _subscription(descriptor):
    return {"provider": "plugin-market", "provider_package_id": str(descriptor["package_id"]),
            **{key: descriptor[key] for key in ("remote_id", "pack_id", "material_type", "version", "channel")},
            "artifact_sha256": descriptor["artifacts"]["knowledge"]["sha256"]}


@pytest.mark.parametrize("package_id", [1, 9, 10])
def test_numeric_share_identity_survives_both_native_contracts(package_id):
    descriptor = _descriptor(package_id, f"knowledge/{package_id}")
    assert km.KnowledgeVersionDescriptor.model_validate(descriptor).remote_id == descriptor["remote_id"]
    assert parse_subscription(_subscription(descriptor)).remote_id == descriptor["remote_id"]


@pytest.mark.parametrize("remote_id", ["knowledge/", "knowledge/-", "knowledge/A", "knowledge/has space", "knowledge/a/../1"])
def test_malformed_remote_id_is_still_rejected_by_both_contracts(remote_id):
    descriptor = _descriptor(remote_id=remote_id)
    with pytest.raises(ValidationError):
        km.KnowledgeVersionDescriptor.model_validate(descriptor)
    with pytest.raises(SubscriptionError):
        parse_subscription(_subscription(descriptor))


class _FrozenEncoderIdentity:
    """Match the synthetic exporter's identity while forbidding all inference."""

    def __init__(self, spec):
        self._spec = spec
        self.calls = []

    def state(self):
        return "ready"

    def model_id(self):
        return self._spec["model_id"]

    def spec(self):
        return self._spec

    def count_tokens(self, texts):
        # These fixtures used a 1000-character synthetic input budget. The
        # synthetic tokenizer reserves 24 tokens in its 1024-token profile.
        return [len(text) + 24 for text in texts]

    async def embed(self, text):
        self.calls.append(text)
        raise AssertionError("a complete matching archive must reuse its vectors")

    embed_query = embed
    embed_batch = embed
    embed_documents = embed


async def test_real_builder_native_worker_installs_seeded_market_pack(tmp_path, monkeypatch):
    directory = os.environ.get("NEKO_UNIFIED_PACKAGE_FIXTURES")
    if not directory:
        pytest.skip("set NEKO_UNIFIED_PACKAGE_FIXTURES to the synthetic real-builder output directory")
    artifact = Path(directory) / "package-1.0.0.neko-knowledge.zip"
    raw = artifact.read_bytes()
    with zipfile.ZipFile(artifact) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        pack = json.loads(archive.read("pack.json"))
    encoder = _FrozenEncoderIdentity(manifest["embedding_spec"])
    service = KnowledgeService(tmp_path / "knowledge", embedder=encoder)
    await service.start()
    assert service.availability()["ready"]
    monkeypatch.setattr(knowledge_routes, "_service", service)
    monkeypatch.setattr(knowledge_routes, "_import_slots", asyncio.Semaphore(knowledge_routes.MAX_PENDING_IMPORTS))
    # Real proxy responses update Main's availability cache. Restore the
    # caller's original values after the synthetic service goes away.
    availability = public_knowledge_router.public_knowledge
    for name in ("_tool_available", "_confirmed_at", "_next_check_at"):
        monkeypatch.setattr(availability, name, getattr(availability, name))
    # Runtime state belongs to this test; no live plugin workers are touched.
    for name in ("_tasks", "_task_workers", "_applies", "_active_package_tasks", "_unsubscribing", "_mirror_cache", "_reports"):
        monkeypatch.setattr(km, name, {})
    monkeypatch.setattr(km, "_background", set())
    monkeypatch.setattr(km, "MARKET_API_URL", "https://market-acceptance.invalid")
    monkeypatch.setattr(km, "_main_server_port", lambda: 48990)
    import config

    monkeypatch.setattr(config, "AUTOSTART_CSRF_TOKEN", "synthetic-csrf-token")
    monkeypatch.setattr(system_shared, "AUTOSTART_CSRF_TOKEN", "synthetic-csrf-token")
    descriptor = _descriptor(pack_id=pack["pack_id"], raw=raw, version=manifest["version"])
    market_requests, main_requests, memory_requests = [], [], []

    def market(request):
        """Mock remote bytes only; retain real catalog parsing/download checks."""
        market_requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-market-token"
        if request.url.path == "/api/v1/knowledge/packages/1/versions/1.0.0":
            assert request.url.params["format_version"] == "2"
            return httpx.Response(200, json=descriptor)
        if request.url.path == "/api/v1/shares/1/download":
            assert request.url.params["revision"] == "1"
            assert request.url.params["file_sha256"] == hashlib.sha256(raw).hexdigest()
            return httpx.Response(200, content=raw, headers={"Content-Type": "application/zip"})
        assert request.method == "POST" and request.url.path == "/api/v1/me/knowledge-subscriptions"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(market)

    async def external_httpx():
        return SimpleNamespace(AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
                               Timeout=httpx.Timeout, HTTPError=httpx.HTTPError)

    async def oauth(**_kwargs):
        return {"access_token": "synthetic-market-token"}

    async def record_main(request):
        await request.aread()
        main_requests.append(request)

    async def record_memory(request):
        await request.aread()
        memory_requests.append(request)

    monkeypatch.setattr(km, "ensure_httpx", external_httpx)
    monkeypatch.setattr(km, "_ensure_valid_oauth_token", oauth)
    memory_app, main_app, plugin_app = FastAPI(), FastAPI(), FastAPI()
    memory_app.include_router(knowledge_routes.router)
    main_app.include_router(public_knowledge_router.router)
    plugin_app.include_router(km.router)
    try:
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=memory_app), event_hooks={"request": [record_memory]}) as memory,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=main_app), base_url="http://127.0.0.1:48990",
                              event_hooks={"request": [record_main]}) as main,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=plugin_app), base_url="http://127.0.0.1") as plugin,
        ):
            monkeypatch.setattr(km, "_client", lambda: main)
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: memory)
            token = {"token": get_bridge_token()}
            accepted = await plugin.post("/market/knowledge/subscribe", params=token,
                                         json={"package_id": 1, "version": manifest["version"], "channel": "stable", "pack_id": pack["pack_id"]})
            assert accepted.status_code == 200, accepted.text
            async with asyncio.timeout(10):
                while True:
                    task = (await plugin.get(f"/market/knowledge/tasks/{accepted.json()['task_id']}", params=token)).json()
                    if task["status"] in {"completed", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.01)
            assert task["status"] == "completed" and task["stage"] == "staged", task
            async with asyncio.timeout(10):
                while True:
                    jobs = (await main.get("/api/public-knowledge/packs/jobs")).json()["jobs"]
                    job = next(item for item in jobs if item["job_id"] == task["result"]["job_id"])
                    if job["state"] in {"active", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.01)
            assert job["state"] == "active", job
            installed = (await main.get("/api/public-knowledge/packs")).json()["packs"]
            assert len(installed) == 1
            assert installed[0]["origin"] == "market" and installed[0]["pack_id"] == pack["pack_id"]
            assert installed[0]["subscription"] == _subscription(descriptor)
            assert installed[0]["chunks_ready"] == installed[0]["chunks_total"] == manifest["counts"]["chunks"]
            reconciled = (await plugin.get("/market/knowledge/subscriptions", params=token)).json()
            assert reconciled["subscriptions"][0]["subscription"] == _subscription(descriptor)

        # Main validates the real native CSRF+Origin, then removes browser
        # headers while forwarding the same protocol-2 multipart to Memory.
        forwarded = next(request for request in main_requests if request.url.path.endswith("/subscriptions/apply"))
        received = next(request for request in memory_requests if request.url.path.endswith("/subscriptions/apply"))
        assert forwarded.headers["origin"] == "http://127.0.0.1:48990"
        assert forwarded.headers["x-csrf-token"] == "synthetic-csrf-token"
        assert "origin" not in received.headers and "referer" not in received.headers
        assert b'name="protocol_version"\r\n\r\n2\r\n' in received.content
        assert b'filename="pack.neko-knowledge.zip"' in received.content and raw in received.content
        assert received.content == forwarded.content
        assert any(request.url.path == "/api/v1/shares/1/download" for request in market_requests)
        with sqlite3.connect(service._store.database_path) as connection:
            rows = connection.execute("SELECT vector,model_id,spec_sha256,attempts FROM chunks").fetchall()
        assert len(rows) == manifest["counts"]["chunks"]
        assert all(vector and model == encoder.model_id() and spec_hash and attempts == 0
                   for vector, model, spec_hash, attempts in rows)
        assert service._store.vector_specification(encoder.model_id(), pack_id=pack["pack_id"]) == encoder.spec()
        assert encoder.calls == []
    finally:
        tasks = set(km._task_workers.values()) | set(km._applies.values()) | km._background
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.stop()

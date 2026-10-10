"""Memory Server ``/internal/knowledge/*`` routes.

The routes run on the real Memory Server app, so they inherit its middleware
and storage startup gate; these tests exercise the handlers on a bare app and
check one thing on the real one: that the router is mounted there.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService


pytestmark = pytest.mark.unit


PACK = {
    "schema_version": 1,
    "pack_id": "route-pack",
    "material_type": "knowledge",
    "source": {"name": "Route", "homepage": "", "license": "CC0-1.0"},
    "entries": [{"title": "Kotatsu", "summary": "A heated table.", "content": "A kotatsu is a low heated table."}],
}


@pytest.fixture
async def client(tmp_path, monkeypatch):
    service = KnowledgeService(tmp_path)
    await service.start()
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http, service
    await service.stop()


async def _wait_active(http) -> None:
    for _ in range(200):
        jobs = (await http.get("/internal/knowledge/packs/jobs")).json()["jobs"]
        if jobs and jobs[0]["state"] == "active":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


def test_router_is_mounted_on_the_memory_server_app():
    from app.memory_server import runtime
    from tests.fastapi_routes import effective_path, iter_routes

    paths = {effective_path(route) for route in iter_routes(runtime.app.routes)}
    assert "/internal/knowledge/query" in paths
    assert "/internal/knowledge/packs/import" in paths


async def test_storage_startup_gate_covers_knowledge_routes(monkeypatch):
    """Before the memory runtime is initialized, knowledge is gated like memory."""
    from app.memory_server import runtime

    monkeypatch.setattr(runtime, "_memory_runtime_init_completed", False)
    transport = httpx.ASGITransport(app=runtime.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48912") as http:
        response = await http.post("/internal/knowledge/query", json={"query": "x"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "storage_startup_blocked"


async def test_import_then_query_reports_tool_availability(client):
    http, _service = client
    response = await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK))
    body = response.json()
    assert body["ok"] is True and body["state"] == "queued"
    await _wait_active(http)
    availability = (await http.get("/internal/knowledge/availability")).json()
    assert availability["tool_available"] is True

    result = (await http.post("/internal/knowledge/query", json={"query": "kotatsu"})).json()
    assert result["result"] == "matched"
    assert result["tool_available"] is True
    assert "Kotatsu" in result["context"]


async def test_management_reads_and_writes(client):
    http, _service = client
    await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK))
    await _wait_active(http)
    status = (await http.get("/internal/knowledge/status")).json()
    assert status["ok"] is True and status["status"]["entries"] == 1
    entries = (await http.get("/internal/knowledge/entries", params={"query": "kotatsu"})).json()
    assert entries["items"][0]["title"] == "Kotatsu"
    entry = (await http.get("/internal/knowledge/entry", params={"pack_id": "route-pack", "title": "Kotatsu"})).json()
    assert entry["entry"]["content"].startswith("A kotatsu")
    toggled = (await http.post("/internal/knowledge/packs/auto-context", json={"pack_id": "route-pack", "enabled": True})).json()
    assert toggled["auto_context"] is True
    disabled = (await http.post("/internal/knowledge/entry/disabled", json={"pack_id": "route-pack", "title": "Kotatsu", "disabled": True})).json()
    assert disabled["ok"] is True and disabled["tool_available"] is False
    removed = (await http.post("/internal/knowledge/packs/remove", json={"pack_id": "route-pack"})).json()
    assert removed["ok"] is True
    missing = (await http.post("/internal/knowledge/packs/remove", json={"pack_id": "route-pack"})).json()
    assert missing == {"ok": False, "reason": "not_found", "ready": True, "enabled": True, "tool_available": False}


async def test_invalid_requests_are_rejected(client):
    http, _service = client
    assert (await http.post("/internal/knowledge/query", content=b"[1]")).status_code == 400
    assert (await http.post("/internal/knowledge/settings", json={"enabled": "yes"})).status_code == 400
    too_large = await http.post(
        "/internal/knowledge/packs/import", content=b"x" * (knowledge_routes._PACK_BODY_MAX_BYTES + 1)
    )
    assert too_large.status_code == 413


async def test_unexpected_knowledge_errors_stay_inside_the_handler(client, monkeypatch):
    http, service = client

    async def boom(**_kwargs):
        raise RuntimeError("knowledge bug")

    monkeypatch.setattr(service, "list_entries", boom)
    response = await http.get("/internal/knowledge/entries")
    assert response.status_code == 500
    assert response.json() == {"ok": False, "reason": "knowledge_error"}


async def test_not_started_reports_starting(monkeypatch):
    monkeypatch.setattr(knowledge_routes, "_service", None)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        assert (await http.get("/internal/knowledge/availability")).json()["tool_available"] is False
        response = await http.post("/internal/knowledge/query", json={"query": "x"})
        assert response.status_code == 503


async def test_multipart_import_uses_the_uploaded_file(client):
    http, _service = client
    files = {"pack": ("pack.json", json.dumps(PACK).encode(), "application/json")}
    response = (await http.post("/internal/knowledge/packs/import", files=files)).json()
    assert response["ok"] is True and response["pack_id"] == "route-pack"
    await _wait_active(http)
    bad = await http.post("/internal/knowledge/packs/import", files={"a": ("a", b"1"), "b": ("b", b"2")})
    assert bad.status_code == 400


async def test_long_catalog_searches_are_cut_not_refused(client):
    http, _service = client
    await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK))
    await _wait_active(http)
    response = await http.get("/internal/knowledge/entries", params={"query": "kotatsu " * 100})
    assert response.status_code == 200
    assert response.json()["ok"] is True


async def test_imports_are_refused_before_their_body_is_read_when_full(client, monkeypatch):
    http, _service = client
    read = []
    real_read = knowledge_routes._read_body

    async def recording(request, *, max_bytes):
        read.append(max_bytes)
        return await real_read(request, max_bytes=max_bytes)

    monkeypatch.setattr(knowledge_routes, "_read_body", recording)
    monkeypatch.setattr(knowledge_routes, "_import_slots", asyncio.Semaphore(0))  # all taken
    # Bounded: a route that waited for a slot instead of refusing would hang.
    result = (
        await asyncio.wait_for(http.post("/internal/knowledge/packs/import", content=json.dumps(PACK)), 5)
    ).json()
    assert result["ok"] is False and result["reason"] == "knowledge_busy"
    assert read == []
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(knowledge_routes, "_import_slots", slots)
    result = (await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK))).json()
    assert result["ok"] is True
    assert not slots.locked()  # released after the request


async def test_knowledge_embeddings_never_disable_vectors_for_memory(monkeypatch):
    from memory import embeddings

    monkeypatch.setattr(embeddings, "detect_total_ram_gb", lambda: 12.0)
    monkeypatch.setattr(embeddings, "detect_avx_vnni_details", lambda: (False, True))
    monkeypatch.setattr(embeddings, "detect_avx2_details", lambda: (True, True))
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir="/nonexistent")
    service._state = embeddings.EmbeddingState.READY

    def broken(_texts):
        raise RuntimeError("tokenizer choked on third-party text")

    monkeypatch.setattr(service, "_infer_blocking", broken)
    shared = knowledge_routes._SharedEmbedder(service)
    assert await shared.embed_batch(["x", "y"]) == [None, None]
    assert await shared.embed("x") is None
    assert service.is_available()  # memory keeps its vectors
    # A failure of memory's own call is still sticky, as before.
    assert await service.embed_batch(["x"]) == [None]
    assert service.is_disabled()


def test_proxy_body_limit_matches_the_pack_limit():
    from knowledge.models import MAX_PACK_BYTES
    from utils.http import knowledge_proxy

    # utils cannot import knowledge, so the proxies keep their own copy.
    assert knowledge_proxy.PACK_BODY_MAX_BYTES == MAX_PACK_BYTES + 64 * 1024
    assert knowledge_routes._PACK_BODY_MAX_BYTES == knowledge_proxy.PACK_BODY_MAX_BYTES
    assert knowledge_proxy.WRITE_PATHS["packs/import"] == knowledge_proxy.PACK_BODY_MAX_BYTES


def _ready_embedding_service(monkeypatch):
    from memory import embeddings

    monkeypatch.setattr(embeddings, "detect_total_ram_gb", lambda: 12.0)
    monkeypatch.setattr(embeddings, "detect_avx_vnni_details", lambda: (False, True))
    monkeypatch.setattr(embeddings, "detect_avx2_details", lambda: (True, True))
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    service = embeddings.EmbeddingService(model_dir="/nonexistent")
    service._state = embeddings.EmbeddingState.READY
    return service


async def test_one_bad_text_does_not_fail_its_whole_batch(monkeypatch):
    service = _ready_embedding_service(monkeypatch)

    def infer(texts):
        if "poison" in texts:
            raise RuntimeError("tokenizer choked")
        return [[float(len(text))] for text in texts]

    monkeypatch.setattr(service, "_infer_blocking", infer)
    shared = knowledge_routes._SharedEmbedder(service)
    assert await shared.embed_batch(["ok", "poison", "fine"]) == [[2.0], None, [4.0]]
    assert service.is_available()


async def test_non_sticky_failures_are_logged_at_most_once_a_minute(monkeypatch):
    from memory import embeddings

    service = _ready_embedding_service(monkeypatch)
    warnings = []
    monkeypatch.setattr(embeddings.logger, "warning", lambda *args, **kwargs: warnings.append(args))
    for _ in range(5):
        service._on_inference_error(RuntimeError("boom"), sticky=False)
    assert len(warnings) == 1
    assert service.is_available()


async def test_items_after_failing_ones_are_still_tried(monkeypatch):
    service = _ready_embedding_service(monkeypatch)

    def infer(texts):
        if any(text.startswith("bad") for text in texts):
            raise RuntimeError("tokenizer choked")
        return [[float(len(text))] for text in texts]

    monkeypatch.setattr(service, "_infer_blocking", infer)
    shared = knowledge_routes._SharedEmbedder(service)
    # Two separate bad texts first: the healthy rest is not failed untried.
    assert await shared.embed_batch(["bad1", "bad2", "ok", "fine"]) == [None, None, [2.0], [4.0]]


async def test_cross_site_browser_requests_are_refused(client):
    http, service = client
    evil = {"Origin": "https://evil.example"}
    # A CORS-simple text/plain POST, as any web page can send to a fixed port.
    settings = await http.post(
        "/internal/knowledge/settings",
        content=b'{"enabled": false}',
        headers={**evil, "Content-Type": "text/plain"},
    )
    assert settings.status_code == 403
    # An auto-submitted HTML form.
    files = {"pack": ("pack.json", json.dumps(PACK).encode(), "application/json")}
    form = await http.post("/internal/knowledge/packs/import", files=files, headers=evil)
    assert form.status_code == 403
    referer_only = await http.post(
        "/internal/knowledge/packs/remove",
        json={"pack_id": "route-pack"},
        headers={"Referer": "https://evil.example/page"},
    )
    assert referer_only.status_code == 403
    assert service.availability()["enabled"] is True
    assert service.list_jobs() == []


async def test_json_routes_take_only_json_bodies(client):
    http, service = client
    plain = await http.post(
        "/internal/knowledge/settings", content=b'{"enabled": false}', headers={"Content-Type": "text/plain"}
    )
    assert plain.status_code == 400
    assert service.availability()["enabled"] is True
    # Main and the plugin server send JSON with no browser origin; a page of
    # the app itself (loopback origin) is allowed as well.
    typed = await http.post(
        "/internal/knowledge/settings",
        content=b'{"enabled": false}',
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert typed.status_code == 200 and typed.json()["enabled"] is False
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48912") as local_http:
        local = await local_http.post(
            "/internal/knowledge/settings", json={"enabled": True}, headers={"Origin": "http://127.0.0.1:48911"}
        )
    assert local.status_code == 200 and local.json()["enabled"] is True

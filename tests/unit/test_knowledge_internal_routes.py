"""Memory Server ``/internal/knowledge/*`` routes.

The routes run on the real Memory Server app, so they inherit its middleware
and storage startup gate; these tests exercise the handlers on a bare app and
check one thing on the real one: that the router is mounted there.
"""

from __future__ import annotations

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
    import asyncio

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

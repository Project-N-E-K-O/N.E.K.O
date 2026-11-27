"""Memory Server ``/internal/knowledge/*`` routes.

The routes run on the real Memory Server app, so they inherit its middleware
and storage startup gate; these tests exercise the handlers on a bare app and
check one thing on the real one: that the router is mounted there.
"""

from __future__ import annotations

import asyncio
import json
import hashlib
from urllib.parse import unquote

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
    response = await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"})
    body = response.json()
    assert body["ok"] is True and body["state"] == "queued"
    await _wait_active(http)
    availability = (await http.get("/internal/knowledge/availability")).json()
    assert availability["tool_available"] is False
    pack = (await http.get("/internal/knowledge/packs")).json()["packs"][0]
    assert pack["enabled"] is False and pack["origin"] == "imported" and pack["sources_managed"] is False
    # Local packages can contain private text; explicit activation is required
    # before the existing query and availability behavior is exercised.
    await http.post("/internal/knowledge/packs/enabled", json={
        "pack_id": "route-pack", "enabled": True, "expected_pack_sha256": pack["pack_sha256"],
    })

    result = (await http.post("/internal/knowledge/query", json={"query": "kotatsu"})).json()
    assert result["result"] == "matched"
    assert result["tool_available"] is True
    assert "Kotatsu" in result["context"]


@pytest.mark.parametrize("global_enabled, pack_enabled", [(True, False), (False, True), (True, True), (False, False)])
async def test_pack_toggle_keeps_pack_enabled_distinct_from_global_availability(client, global_enabled, pack_enabled):
    http, service = client
    imported = await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"})
    assert imported.json()["ok"] is True
    await _wait_active(http)
    await service.set_pack_enabled("route-pack", not pack_enabled)
    settings = (await http.post("/internal/knowledge/settings", json={"enabled": global_enabled})).json()
    assert settings["ok"] is True and settings["enabled"] is global_enabled

    # Repeating the write checks both a changed pack and an already-matching
    # pack without conflating either state with the global envelope switch.
    for _ in range(2):
        payload = {"pack_id": "route-pack", "enabled": pack_enabled}
        if pack_enabled:
            payload["expected_pack_sha256"] = service._registry.packs["route-pack"].pack_sha256
        response = await http.post("/internal/knowledge/packs/enabled", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True and body["pack_id"] == "route-pack"
        persisted = json.loads((service.root / "registry.json").read_text())
        assert persisted["enabled"] is global_enabled
        assert persisted["packs"]["route-pack"]["enabled"] is pack_enabled
        listed = (await http.get("/internal/knowledge/packs")).json()["packs"][0]
        assert listed["enabled"] is pack_enabled
        assert body["enabled"] is global_enabled
        assert body["ready"] is True and body["tool_available"] is (global_enabled and pack_enabled)
        assert body["pack_enabled"] is pack_enabled


async def test_local_zip_defaults_disabled_without_fabricating_original_history(client, tmp_path):
    from tests.knowledge_archive_fixtures import make_archive

    http, service = client
    raw = make_archive(tmp_path / "source.zip").read_bytes()
    response = await http.post("/internal/knowledge/packs/import", content=raw, headers={"Content-Type": "application/zip"})
    assert response.json()["ok"] is True
    await _wait_active(http)
    record = (await service.list_packs())[0]
    assert not record["enabled"] and record["origin"] == "imported" and not record["sources_managed"]
    sources = (await http.get("/internal/knowledge/sources", params={"pack_id": record["pack_id"]})).json()
    assert sources["ok"] and sources["sources"] == []
    appended = await http.post("/internal/knowledge/sources/preview", params={"pack_id": record["pack_id"]},
                               content=b"Never replace a completed package.",
                               headers={"X-Knowledge-Filename": "notes.txt", "Content-Type": "application/octet-stream"})
    assert appended.json()["ok"] is False and appended.json()["reason"] == "invalid_request"


async def test_management_reads_and_writes(client):
    http, _service = client
    await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"})
    await _wait_active(http)
    status = (await http.get("/internal/knowledge/status")).json()
    assert status["ok"] is True and status["status"]["entries"] == 1
    entries = (await http.get("/internal/knowledge/entries", headers={"X-Knowledge-Query": "kotatsu"})).json()
    assert entries["items"][0]["title"] == "Kotatsu"
    entry = (await http.get("/internal/knowledge/entry", params={"pack_id": "route-pack"}, headers={"X-Knowledge-Title": "Kotatsu"})).json()
    assert entry["entry"]["content"].startswith("A kotatsu")
    toggled = (await http.post("/internal/knowledge/packs/auto-context", json={"pack_id": "route-pack", "enabled": True})).json()
    assert toggled["auto_context"] is True
    disabled = (await http.post("/internal/knowledge/entry/disabled", json={"pack_id": "route-pack", "title": "Kotatsu", "disabled": True})).json()
    assert disabled["ok"] is True and disabled["tool_available"] is False
    # Availability is already false for a default-disabled pack; read the
    # entry itself to establish that the management write actually landed.
    persisted = (await http.get("/internal/knowledge/entry", params={"pack_id": "route-pack"},
                                headers={"X-Knowledge-Title": "Kotatsu"})).json()
    assert persisted["entry"]["disabled"] is True
    removed = (await http.post("/internal/knowledge/packs/remove", json={"pack_id": "route-pack"})).json()
    assert removed["ok"] is True
    missing = (await http.post("/internal/knowledge/packs/remove", json={"pack_id": "route-pack"})).json()
    assert missing == {"ok": False, "reason": "not_found", "ready": True, "enabled": True, "tool_available": False}


async def test_invalid_requests_are_rejected(client):
    http, _service = client
    assert (await http.post("/internal/knowledge/query", content=b"[1]")).status_code == 400
    assert (await http.post("/internal/knowledge/settings", json={"enabled": "yes"})).status_code == 400
    too_large = await http.post(
        "/internal/knowledge/packs/import",
        content=b"x" * (knowledge_routes._PACK_BODY_MAX_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert too_large.status_code == 413


async def test_export_route_admits_only_complete_package_requests(client, monkeypatch):
    """Neither a retired selector nor a wrong JSON type can reach export."""
    http, service = client
    calls = []

    async def export(pack_id, *, format_version):
        calls.append((pack_id, format_version))
        return {"ok": True}

    monkeypatch.setattr(service, "export_pack", export)
    for version in (1, True, 2.0, "2", None):
        response = await http.post("/internal/knowledge/packs/export", json={"pack_id": "route-pack", "format_version": version})
        assert response.status_code == 400 and response.json()["reason"] == "invalid_request"
    assert not calls
    for manual in ("1.0.0", "arbitrary input", 1, None):
        response = await http.post("/internal/knowledge/packs/export", json={"pack_id": "route-pack", "version": manual})
        assert response.status_code == 400 and response.json()["reason"] == "invalid_request"
    assert not calls
    for extra in ({}, {"format_version": 2}):
        response = await http.post("/internal/knowledge/packs/export", json={"pack_id": "route-pack", **extra})
        assert response.status_code == 200
    assert calls == [("route-pack", 2)] * 2


async def test_delete_export_route_validates_body_and_removes_the_completed_card(client):
    """The public action accepts a job identity, never a client file path."""
    from knowledge.registry import utc_now
    from knowledge.service import ImportJob

    http, service = client
    job_id, digest = "a" * 32, "b" * 64
    stamp = utc_now()
    job = ImportJob(job_id, "route-pack", "active", stamp, stamp,
                    kind="pack_export", result={"artifact_id": digest})
    service._remember_job(job)
    await service._persist_job(job)
    artifact = service.root / "artifacts" / (digest + ".neko-knowledge.zip")
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(b"synthetic export")
    for body in ({}, {"job_id": None}, {"job_id": job_id, "filename": "private"}):
        response = await http.post("/internal/knowledge/packs/exports/delete", json=body)
        assert response.status_code == 400 and response.json()["reason"] == "invalid_request"
    refused = await http.post("/internal/knowledge/packs/exports/delete", json={"job_id": "../private"})
    assert refused.json()["ok"] is False and refused.json()["reason"] == "invalid_request"
    assert artifact.exists() and job_id in service._jobs
    response = await http.post("/internal/knowledge/packs/exports/delete", json={"job_id": job_id})
    assert response.json()["ok"] is True and response.json()["job_id"] == job_id
    assert response.json()["artifact_retained"] is False
    assert not artifact.exists() and job_id not in service._jobs


async def test_artifact_route_serves_zip_and_preserves_retired_json_without_serving_it(client):
    """Removing the JSON download path must not delete historical bytes."""
    from tests.knowledge_archive_fixtures import make_archive

    http, service = client
    folder = service.root / "artifacts"
    folder.mkdir()
    text = json.dumps(PACK).encode()
    legacy_id = hashlib.sha256(text).hexdigest()
    legacy = folder / (legacy_id + ".neko-knowledge.json")
    legacy.write_bytes(text)
    source = make_archive(service.root / "source.zip").read_bytes()
    artifact_id = hashlib.sha256(source).hexdigest()
    (folder / (artifact_id + ".neko-knowledge.zip")).write_bytes(source)
    refused = await http.get("/internal/knowledge/packs/artifacts", params={"artifact_id": legacy_id})
    assert refused.status_code == 404
    assert legacy.read_bytes() == text
    response = await http.get("/internal/knowledge/packs/artifacts", params={"artifact_id": artifact_id})
    assert response.status_code == 200 and response.content == source
    assert response.headers["content-type"] == "application/zip"
    assert ".neko-knowledge.zip" in response.headers["content-disposition"]


async def test_artifact_download_uses_frozen_readable_name_for_old_and_new_results(client):
    """HTTP and UI names agree without renaming stored archives or old tasks."""
    from tests.knowledge_fixtures import _import, _pack
    from tests.knowledge_fixtures import _finished
    from tests.knowledge_archive_fixtures import _prepare_export_vectors

    http, service = client
    payload = _pack()
    payload["source"]["name"] = "我的资料"
    await _import(service, payload)
    _prepare_export_vectors(service, payload["pack_id"])
    finished = await _finished(service, await service.export_pack(payload["pack_id"]))
    result = finished["result"]
    filename = result["filename"]
    assert filename.startswith("我的资料_")
    assert result["version"] not in filename and result["sha256"] not in filename
    path = service.artifact_path(result["artifact_id"])
    assert path.name == result["sha256"] + ".neko-knowledge.zip"
    before = path.read_bytes()
    # Simulate pre-improvement metadata and then replace today's pack name.
    # The immutable ZIP remains the authority for this old export's title.
    old_name = payload["pack_id"] + "." + result["version"] + ".neko-knowledge.zip"
    service._jobs[finished["job_id"]].result["filename"] = old_name
    payload["source"]["name"] = "Renamed current pack"
    await _import(service, payload)
    response = await http.get("/internal/knowledge/packs/artifacts", params={"artifact_id": result["artifact_id"]})
    assert response.status_code == 200 and response.content == before
    disposition = response.headers["content-disposition"]
    assert unquote(disposition.split("filename*=utf-8''", 1)[1]) == filename
    assert service._jobs[finished["job_id"]].result["filename"] == old_name
    assert path.read_bytes() == before


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
    await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"})
    await _wait_active(http)
    response = await http.get("/internal/knowledge/entries", headers={"X-Knowledge-Query": "kotatsu " * 100})
    assert response.status_code == 200
    assert response.json()["ok"] is True


async def test_management_read_identity_is_decoded_from_headers_not_retired_url_fields(client, monkeypatch):
    """Unicode private fields have no query-string fallback or log exposure."""
    from urllib.parse import quote

    http, service = client
    calls = []

    async def entries(**kwargs):
        calls.append(kwargs)
        return {"items": []}

    async def entry(pack_id, title):
        calls.append({"pack_id": pack_id, "title": title})
        return {"entry": {}}

    monkeypatch.setattr(service, "list_entries", entries)
    monkeypatch.setattr(service, "get_entry", entry)
    assert (await http.get("/internal/knowledge/entries", params={"query": "ignored"},
                           headers={"X-Knowledge-Query": quote("私密查询")})).status_code == 200
    assert calls[-1]["query"] == "私密查询"
    assert (await http.get("/internal/knowledge/entry", params={"pack_id": "route-pack", "title": "ignored"},
                           headers={"X-Knowledge-Title": quote("私密文件.txt")})).status_code == 200
    assert calls[-1]["title"] == "私密文件.txt"
    assert (await http.get("/internal/knowledge/entry", params={"pack_id": "route-pack", "title": "ignored"})).status_code == 400
    assert (await http.get("/internal/knowledge/entries", headers={"X-Knowledge-Query": "%FF"})).status_code == 400


async def test_source_preview_never_accepts_a_private_filename_from_the_url(client):
    """An old URL field cannot replace the required private display header."""
    http, _service = client
    response = await http.post("/internal/knowledge/sources/preview", params={"filename": "private.txt", "name": "private-group"},
                               content=b"Synthetic original", headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 400 and response.json()["reason"] == "invalid_request"


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
        await asyncio.wait_for(http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"}), 5)
    ).json()
    assert result["ok"] is False and result["reason"] == "knowledge_busy"
    assert read == []
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(knowledge_routes, "_import_slots", slots)
    result = (await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers={"Content-Type": "application/json"})).json()
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
    from utils.knowledge_package import DEFAULT_LIMITS
    from utils.http import knowledge_proxy

    # utils cannot import knowledge, so the proxies keep their own copy.
    assert knowledge_proxy.PACK_BODY_MAX_BYTES == DEFAULT_LIMITS.max_archive_bytes + 64 * 1024
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
    # Main and the plugin server send JSON with no browser origin.
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
        # A page another local app serves on a loopback port is a browser too.
        local = await local_http.post(
            "/internal/knowledge/settings", json={"enabled": True}, headers={"Origin": "http://127.0.0.1:5173"}
        )
        form = await local_http.post(
            "/internal/knowledge/packs/import",
            files={"pack": ("pack.json", json.dumps(PACK).encode(), "application/json")},
            headers={"Origin": "http://localhost:8080"},
        )
        native = await local_http.post("/internal/knowledge/settings", json={"enabled": True})
    assert local.status_code == 403 and form.status_code == 403
    assert service.list_jobs() == []
    assert native.status_code == 200 and native.json()["enabled"] is True


async def test_imports_take_only_json_or_multipart_bodies(client):
    http, service = client
    for headers in ({"Content-Type": "text/plain"}, {}):
        response = await http.post("/internal/knowledge/packs/import", content=json.dumps(PACK), headers=headers)
        assert response.status_code == 400
    assert service.list_jobs() == []


def test_turn_context_route_is_mounted_on_the_memory_server_app():
    from app.memory_server import runtime
    from tests.fastapi_routes import effective_path, iter_routes

    paths = {effective_path(route) for route in iter_routes(runtime.app.routes)}
    assert "/internal/knowledge/turn_context" in paths
    assert "/internal/memory/{lanlan_name}/turn_context" in paths


async def test_turn_context_route_validates_and_passes_exclusions(client, monkeypatch):
    http, service = client
    response = await http.post("/internal/knowledge/turn_context", json={"text": 3})
    assert response.status_code == 400
    seen = {}

    async def fake(**kwargs):
        seen.update(kwargs)
        return {"result": "miss"}

    monkeypatch.setattr(service, "turn_context", fake)
    excluded = ["a/1", 5, *[f"p/{i}" for i in range(400)]]
    body = (await http.post(
        "/internal/knowledge/turn_context",
        json={"text": "你好", "exclude_card_ids": excluded, "budget_ms": True, "language": "en"},
    )).json()
    assert body["result"] == "miss" and "tool_available" in body
    assert seen["text"] == "你好" and seen["language"] == "en"
    assert seen["budget_ms"] == 1000, "a non-integer budget falls back to the default"
    assert seen["exclude_card_ids"][0] == "a/1" and 5 not in seen["exclude_card_ids"]
    assert len(seen["exclude_card_ids"]) == 256

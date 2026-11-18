"""Exercise the page's actual HTTP contract against an isolated domain host."""

import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from config import AUTOSTART_CSRF_TOKEN
from main_routers import knowledge_library_router as routes
from services.knowledge_library.host import LibraryHost

pytestmark = pytest.mark.unit
HEADERS = {"Origin": "http://test", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN}


class Vectors:
    """Deterministic encoder used only as an injected computational dependency."""

    def __init__(self):
        self.calls = 0

    async def specification(self):
        return {"model_id": "local-text-retrieval-v1-64d-fp32-mlen1024", "profile": "local-text-retrieval-v1",
                "dimension": 64, "quantization": "fp32", "max_length": 1024,
                "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")},
                "processing_version": "neko-onnx-last-token-truncate-l2-v1"}

    async def plan_inputs(self, inputs, **_kwargs):
        """Keep short route fixtures whole while exercising the planner contract."""
        return [[{"start": 0, "end": len(row["body"])}] for row in inputs]

    async def embed_batch(self, texts, **_kwargs):
        self.calls += len(texts)
        return [[1.0] + [0.0] * 63 for _ in texts]


@pytest.fixture
async def client(tmp_path, monkeypatch):
    """Stub only app ownership dependencies; retain real parsing, jobs and routes."""
    monkeypatch.setattr(routes, "get_config_manager", lambda: SimpleNamespace(app_docs_dir=tmp_path))
    monkeypatch.setattr(LibraryHost, "write_guard", lambda self: nullcontext())
    monkeypatch.setattr("services.knowledge_library.host.HttpEmbeddingAdapter", Vectors)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http, app, tmp_path
    host = getattr(app.state, "knowledge_library_host", None)
    if host is not None:
        await host.close()


async def wait_job(http, job):
    """Observe real terminal server facts, without guessing from elapsed time."""
    for _ in range(300):
        result = (await http.get("/api/knowledge-library/jobs/" + job["job_id"])).json()["job"]
        if result["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            assert result["state"] == "completed", result
            return result["result"]
        await asyncio.sleep(0.01)
    raise AssertionError("job did not terminate")


async def import_text(http, library_id, name="reference.txt", text="知识库保存真实资料。A kotatsu is a heated table."):
    response = await http.post(f"/api/knowledge-library/libraries/{library_id}/documents",
                               files={"file": (name, text.encode(), "text/plain")}, headers=HEADERS)
    assert response.status_code == 200, response.text
    preview_result = await wait_job(http, response.json()["job"])
    preview = (await http.get("/api/knowledge-library/previews/" + preview_result["preview_id"])).json()["preview"]
    response = await http.post("/api/knowledge-library/previews/" + preview["preview_id"] + "/confirm",
                               json={"sha256": preview["sha256"]}, headers=HEADERS)
    await wait_job(http, response.json()["job"])
    return preview


async def test_mutation_rejected_before_storage_or_body_parsing(client):
    http, _app, root = client
    response = await http.post("/api/knowledge-library/libraries", content=b"not json")
    assert response.status_code == 403
    assert not (root / "knowledge_library").exists()


async def test_actual_import_search_build_export_install_contract(client):
    http, app, _root = client
    prefix = "/api/knowledge-library"
    response = await http.post(prefix + "/libraries", json={"name": "My research"}, headers=HEADERS)
    assert response.status_code == 200, response.text
    library_id = response.json()["library"]["library_id"]
    preview = await import_text(http, library_id)
    result = await http.post(f"{prefix}/libraries/{library_id}/search", json={"query": "知识库"}, headers=HEADERS)
    assert result.json()["hits"][0]["text"].startswith("知识库")
    documents = (await http.get(f"{prefix}/libraries/{library_id}")).json()["documents"]
    build = await http.post(f"{prefix}/libraries/{library_id}/packages", headers=HEADERS,
                            json={"version": "1.0.0", "source": {"author": "Test", "license": "CC0-1.0", "url": ""},
                                  "document_ids": [documents[0]["document_id"]]})
    assert build.status_code == 200, build.text
    artifact = await wait_job(http, build.json()["job"])
    assert "artifact_path" not in artifact
    packages = (await http.get(prefix + "/packages")).json()["packages"]
    assert packages[0]["artifact_id"] == artifact["artifact_id"]
    download = await http.get(prefix + "/packages/" + artifact["artifact_id"] + "/download")
    assert download.status_code == 200 and download.content.startswith(b"PK")
    calls = app.state.knowledge_library_host.embedder.calls
    install = await http.post(prefix + "/packages/install", headers=HEADERS,
                              files={"file": ("test.neko-knowledge-pack.zip", download.content, "application/zip")})
    assert install.status_code == 200, install.text
    installed = await wait_job(http, install.json()["job"])
    assert installed["library_id"] != library_id
    assert app.state.knowledge_library_host.embedder.calls == calls  # Installation must not compute document vectors.
    detail = (await http.get(prefix + "/libraries/" + installed["library_id"])).json()
    assert detail["library"]["kind"] == "installed"
    assert "artifact_path" not in detail["library"]["installation"]
    document_id = detail["documents"][0]["document_id"]
    source = (await http.get(f"{prefix}/libraries/{installed['library_id']}/documents/{document_id}")).json()["document"]
    assert source["content"] == preview["content"]
    assert "chunks" not in source and "embedding_spec" not in source
    assert "artifact_path" not in (await http.get(prefix + "/libraries")).text


async def test_failed_preview_public_id_can_be_discarded_and_never_retried(client):
    """Exercise the failure card's real opaque-ID/delete/status/retry contract."""
    http, app, _root = client
    prefix = "/api/knowledge-library"
    library = (await http.post(prefix + "/libraries", json={"name": "Bad input"}, headers=HEADERS)).json()["library"]
    response = await http.post(f"{prefix}/libraries/{library['library_id']}/documents", headers=HEADERS,
                               files={"file": ("damaged.txt", b"\0\0\0\1", "text/plain")})
    service = await app.state.knowledge_library_host.service()
    await service.jobs.queue.join()
    job_id = response.json()["job"]["job_id"]
    job = (await http.get(f"{prefix}/jobs/{job_id}")).json()["job"]
    assert job["state"] == "failed" and job["preview_id"] and not job["input_discarded"]
    assert "frozen_input" not in job
    assert (await http.delete(prefix + "/previews/" + job["preview_id"], headers=HEADERS)).status_code == 200
    assert (await http.get(f"{prefix}/jobs/{job_id}")).json()["job"]["input_discarded"]
    retry = await http.post(f"{prefix}/jobs/{job_id}/retry", headers=HEADERS)
    assert retry.status_code == 400 and retry.json()["reason"] == "preview_input_discarded"


async def test_install_api_requires_explicit_update_destination(client):
    """The unchecked update control sends no target and must preserve the old install."""
    from services.knowledge_library.package_build import build_package
    from tests.unit.knowledge_library.test_packages import Encoder, _snapshot

    http, app, root = client
    prefix = "/api/knowledge-library"
    built = await build_package(_snapshot(), root / "inputs/v1.zip", Encoder())
    updated = await build_package(_snapshot(version="2"), root / "inputs/v2.zip", Encoder())
    async def install(artifact, target=None):
        path = Path(artifact["artifact_path"])
        response = await http.post(prefix + "/packages/install", headers=HEADERS,
                                   files={"file": (path.name, path.read_bytes(), "application/zip")},
                                   data={"library_id": target} if target else {})
        assert response.status_code == 200, response.text
        service = await app.state.knowledge_library_host.service()
        await service.jobs.queue.join()
        return (await http.get(prefix + "/jobs/" + response.json()["job"]["job_id"])).json()["job"]
    first = await install(built)
    identifier = first["result"]["library_id"]
    original = (await http.get(prefix + "/libraries/" + identifier)).json()
    refused = await install(updated)
    assert refused["state"] == "failed" and refused["reason"] == "package_update_requires_target"
    assert (await http.get(prefix + "/libraries/" + identifier)).json() == original
    applied = await install(updated, identifier)
    assert applied["state"] == "completed" and applied["result"]["library_id"] == identifier
    repeated = await install(updated)
    assert repeated["state"] == "completed" and repeated["result"]["library_id"] == identifier


async def test_unknown_fields_bad_files_and_unselected_build_are_rejected(client):
    http, _app, _root = client
    prefix = "/api/knowledge-library"
    assert (await http.post(prefix + "/libraries", json={"name": "x", "path": "/tmp"}, headers=HEADERS)).status_code == 400
    library = (await http.post(prefix + "/libraries", json={"name": "x"}, headers=HEADERS)).json()["library"]
    response = await http.post(f"{prefix}/libraries/{library['library_id']}/packages", json={"document_ids": []}, headers=HEADERS)
    assert response.status_code == 400
    response = await http.post(prefix + "/packages/install", files={"file": ("raw.json", b"{}")}, headers=HEADERS)
    assert response.status_code == 400
    assert (await http.get(prefix + "/packages/../download")).status_code == 404


async def test_invalid_source_shapes_and_non_utf8_names_fail_before_writes(client):
    """Public JSON can carry arrays or lone surrogates, neither is valid metadata."""
    http, _app, _root = client
    prefix = "/api/knowledge-library"
    invalid = await http.post(prefix + "/libraries", content=b'{"name":"\\ud800"}',
                              headers={**HEADERS, "Content-Type": "application/json"})
    assert invalid.status_code == 400 and invalid.json()["reason"] == "invalid_name"
    library = (await http.post(prefix + "/libraries", json={"name": "Valid"}, headers=HEADERS)).json()["library"]
    response = await http.post(f"{prefix}/libraries/{library['library_id']}/packages", headers=HEADERS,
                               json={"version": "1", "source": ["wrong shape"], "document_ids": ["document"]})
    assert response.status_code == 400 and response.json()["reason"] == "invalid_source"


async def test_same_root_cannot_have_two_writers_and_reopens_after_close(client):
    http, app, root = client
    await http.get("/api/knowledge-library/libraries")
    second = LibraryHost(SimpleNamespace(app_docs_dir=root))
    from services.knowledge_library.models import LibraryError
    with pytest.raises(LibraryError, match="storage_busy"):
        await second.service()
    await app.state.knowledge_library_host.close()
    await second.service()
    await second.close()


async def test_page_uses_versioned_static_assets(client, monkeypatch):
    http, app, _root = client
    from main_routers import pages_router
    project = Path(__file__).resolve().parents[3]
    monkeypatch.setattr(pages_router, "get_templates", lambda: Jinja2Templates(directory=str(project)))
    app.include_router(pages_router.router)
    response = await http.get("/knowledge-library")
    assert response.status_code == 200
    assert "/static/js/knowledge_library.js?v=" in response.text
    assert "knowledgeLibrary.title" in response.text


async def test_discard_waits_for_actual_parser_before_reclaiming_input(client):
    """A closed preview cannot delete an original still owned by a worker."""
    import threading

    http, app, root = client
    prefix = "/api/knowledge-library"
    library = (await http.post(prefix + "/libraries", json={"name": "Temporary"}, headers=HEADERS)).json()["library"]
    service = await app.state.knowledge_library_host.service()
    started, release = threading.Event(), threading.Event()
    original_parser = service.parser

    def blocked(*args):
        started.set()
        assert release.wait(5)
        return original_parser(*args)

    service.parser = blocked
    job = (await http.post(f"{prefix}/libraries/{library['library_id']}/documents",
                           files={"file": ("temp.txt", b"An abandoned preview")}, headers=HEADERS)).json()["job"]
    try:
        assert await asyncio.to_thread(started.wait, 3)
        preview_id = service.jobs.records[job["job_id"]]["frozen_input"]["preview_id"]
        path = root / "knowledge_library/staging/previews" / (preview_id + ".original")
        deletion = asyncio.create_task(http.delete(prefix + "/previews/" + preview_id, headers=HEADERS))
        await asyncio.sleep(0.03)
        assert path.exists() and not deletion.done()
        release.set()
        assert (await asyncio.wait_for(deletion, 3)).status_code == 200
        assert not path.exists()
        assert (await http.get(f"{prefix}/libraries/{library['library_id']}")).json()["documents"] == []
    finally:
        release.set()


async def test_upload_admission_rejects_before_reading_another_body(client):
    http, app, _root = client
    await http.get("/api/knowledge-library/libraries")
    host = app.state.knowledge_library_host
    await host.upload_slots.acquire()
    await host.upload_slots.acquire()
    try:
        # A malformed body would normally fail media-type parsing. Under load
        # it must be rejected before another multipart spool is allocated.
        response = await http.post("/api/knowledge-library/packages/install", content=b"unparsed", headers=HEADERS)
        assert response.status_code == 429
    finally:
        host.upload_slots.release()
        host.upload_slots.release()


async def test_deleting_a_selected_document_cancels_uncommitted_build(client):
    http, app, _root = client
    prefix = "/api/knowledge-library"
    library = (await http.post(prefix + "/libraries", json={"name": "Selection"}, headers=HEADERS)).json()["library"]
    await import_text(http, library["library_id"])
    document = (await http.get(f"{prefix}/libraries/{library['library_id']}")).json()["documents"][0]
    service = await app.state.knowledge_library_host.service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(context):
        entered.set()
        await release.wait()
        context.commit_gate()
        return {"must_not_publish": True}

    job = await service.submit_job("build", library["library_id"], pending,
                                   frozen_input={"document_ids": [document["document_id"]]})
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await http.delete(f"{prefix}/libraries/{library['library_id']}/documents/{document['document_id']}", headers=HEADERS)
    finally:
        release.set()
    await asyncio.wait_for(service.jobs.queue.join(), 3)
    assert (await service.get_job(job["job_id"]))["state"] == "cancelled"


def test_long_source_paragraph_keeps_exact_text_and_searchable_tail():
    """Long CJK prose must not gain invented spaces or exceed encoder limits."""
    from services.knowledge_library.indexing import make_chunks

    text = "开始。" + "长段落，原样保留。" * 25_000 + "尾部证据。"
    chunks = make_chunks("document", "source.txt", [{"text": text, "location": {"paragraph": 1}}])
    assert chunks[0]["text"].startswith("开始。")
    assert chunks[-1]["text"].endswith("尾部证据。")
    assert all(chunk["text"] in text and len(chunk["embedding_text"]) <= 2000 for chunk in chunks)
    assert len(chunks) > 96


async def test_cancelled_first_caller_does_not_release_initializing_host(client, monkeypatch):
    """Disconnecting the first reader cannot orphan a worker or writer lock."""
    import threading
    from services.knowledge_library.service import LibraryService

    _http, _app, root = client
    host = LibraryHost(SimpleNamespace(app_docs_dir=root))
    entered, release = threading.Event(), threading.Event()
    original = LibraryService.start

    async def blocked(service):
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        await original(service)

    monkeypatch.setattr(LibraryService, "start", blocked)
    request = asyncio.create_task(host.service())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert host._ownership is not None and host._ownership.is_locked
        release.set()
        service = await asyncio.wait_for(host.service(), 3)
        assert service._started
    finally:
        release.set()
        await host.close()


async def test_host_close_drains_detached_publication_admission(client):
    """The file lock outlives a disconnected publication admission coroutine."""
    _http, _app, root = client
    host = LibraryHost(SimpleNamespace(app_docs_dir=root))
    service = await host.service()
    release = asyncio.Event()

    async def admission():
        await release.wait()
        async def run(_context):
            return {"admitted": True}
        return await service.submit_job("market_publish", None, run, frozen_input={})

    task = asyncio.create_task(admission())
    host._market_publication_tasks = {task}
    closing = asyncio.create_task(host.close())
    try:
        await asyncio.sleep(0.02)
        assert not closing.done() and host._ownership.is_locked
        from services.knowledge_library.models import LibraryError
        with pytest.raises(LibraryError, match="service_stopping"):
            await host.service()
    finally:
        release.set()
        await asyncio.wait_for(closing, 3)
    assert task.done() and host._ownership is None


async def test_startup_reclaims_only_managed_transfers_and_keeps_retry_inputs(client):
    """Hard-exit transfer leftovers are disposable; unfinished upload inputs are not."""
    import json

    _http, _app, root = client
    domain = root / "knowledge_library"
    suffix = ".neko-knowledge-pack.zip"
    retained_id, orphan_id = "a" * 32, "b" * 32
    for name in ("market", "imports"):
        directory = domain / "staging" / name
        directory.mkdir(parents=True)
        (directory / (orphan_id + suffix)).write_bytes(b"orphan")
        (directory / (retained_id + suffix)).write_bytes(b"retained")
        (directory / ("unfamiliar" + suffix)).write_bytes(b"do not touch")
    jobs = domain / "jobs"
    jobs.mkdir()
    record = {"job_id": "c" * 32, "kind": "install", "state": "interrupted", "reason": "process_interrupted",
              "frozen_input": {"upload_id": retained_id}, "library_id": None}
    (jobs / (record["job_id"] + ".json")).write_text(json.dumps(record))
    host = LibraryHost(SimpleNamespace(app_docs_dir=root))
    try:
        await host.service()
        assert (domain / "staging/imports" / (retained_id + suffix)).exists()
        assert not (domain / "staging/imports" / (orphan_id + suffix)).exists()
        assert not (domain / "staging/market" / (retained_id + suffix)).exists()
        assert (domain / "staging/market" / ("unfamiliar" + suffix)).exists()
    finally:
        await host.close()

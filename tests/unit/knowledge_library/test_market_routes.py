"""Exercise Main-owned Market jobs with real packages and isolated fake transports."""

from contextlib import nullcontext
from dataclasses import replace
import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx
import pytest

from main_routers import knowledge_library_market_router as market
from main_routers import knowledge_library_router as local
from services.knowledge_library.package_build import build_package
from services.knowledge_library.jobs import JobContext
from services.knowledge_library.package_validate import validate_package
from services.knowledge_library.service import LibraryService
from tests.unit.knowledge_library.test_packages import Encoder, _snapshot


pytestmark = pytest.mark.unit_fast


@pytest.fixture
async def scenario(tmp_path, monkeypatch):
    """Use only throwaway data and deterministic encoders; no real servers run."""
    service = LibraryService(tmp_path, embedder=Encoder())
    await service.start()
    async def get_service():
        return service
    host = SimpleNamespace(root=tmp_path, embedder=service.embedder, write_guard=nullcontext, service=get_service)
    async def get_host(_request):
        return host
    monkeypatch.setattr(market, "get_host", get_host)
    monkeypatch.setattr(local, "_validate_local_mutation_request", lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(market.router)
    actual_client = httpx.AsyncClient
    client = actual_client(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911")
    built = await build_package(_snapshot(), tmp_path / "artifacts/request.zip", Encoder())
    artifact = Path(built["artifact_path"])
    checked = validate_package(artifact)
    share = {"id": 17, "kind": "knowledge", "title": "Selected notes", "status": "published", "revision": 3,
             "file_sha256": built["sha256"], "byte_size": artifact.stat().st_size,
             "preview": {"format": "neko.personal-knowledge-package", "format_version": 1,
                         "package_id": checked.manifest["package_id"], "package_version": checked.manifest["version"],
                         "embedding_spec": checked.manifest["embedding_spec"]}}
    try:
        yield client, service, host, built, artifact, share, actual_client
    finally:
        await client.aclose()
        await service.close()


def transport(monkeypatch, actual_client, handler, publisher=None):
    """Mock only the fixed bridge network, retaining ASGI request behavior."""
    publisher = publisher if publisher is not None else {"publisher_id": 41, "market_api_url": "https://market.example.test"}
    async def remote(request):
        if request.url.path == "/market/library/publisher":
            return httpx.Response(200, json={"ok": True, **publisher})
        value = handler(request)
        return await value if hasattr(value, "__await__") else value
    fake = httpx.MockTransport(remote)
    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: actual_client(transport=fake, **kwargs))
    async def credentials(_client):
        return "http://bridge.example.test", {"Authorization": "Bearer short-local-bridge"}
    monkeypatch.setattr(market, "_credentials", credentials)


async def complete(service, response):
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    await service.jobs.queue.join()
    return await service.get_job(job["job_id"])


async def test_publish_replays_same_uuid_after_auth_refresh_and_reports_review_state(scenario, monkeypatch):
    client, service, _host, built, artifact, share, actual = scenario
    seen = []
    async def handler(request):
        raw = await request.aread()
        metadata, uploaded = raw.split(b"\n", 1)
        seen.append((json.loads(metadata), uploaded, str(request.url)))
        if len(seen) == 1:
            return httpx.Response(409, json={"detail": {"code": "market_auth_retry"}})
        return httpx.Response(200, json={"ok": True, "share": {**share, "status": "pending_review"}})
    transport(monkeypatch, actual, handler)
    request_id = str(uuid4())
    response = await client.post("/api/knowledge-library/market/publish", json={
        "artifact_id": built["sha256"], "title": "Selected notes", "client_request_id": request_id,
    })
    result = await complete(service, response)
    assert result["state"] == "completed", result
    assert result["result"]["share"]["status"] == "pending_review"
    assert [item[0]["client_request_id"] for item in seen] == [request_id, request_id]
    assert all(item[0]["publisher_id"] == 41 for item in seen)
    assert all(item[1] == artifact.read_bytes() for item in seen)
    assert all(item[2] == "http://bridge.example.test/market/library/publish" for item in seen)
    assert "short-local-bridge" not in json.dumps(result)


async def test_queued_publish_and_retry_never_switch_publishers(scenario, monkeypatch):
    """Freeze the author before queue admission and retain it on explicit retry."""
    client, service, host, built, _artifact, _share, actual = scenario
    publisher = {"publisher_id": 41, "market_api_url": "https://market.example.test"}
    uploaded, entered, release = [], asyncio.Event(), asyncio.Event()
    transport(monkeypatch, actual, lambda request: uploaded.append(request) or httpx.Response(500), publisher)
    async def blocker(_context):
        entered.set()
        await release.wait()
        return {}
    await service.submit_job("test_blocker", None, blocker, frozen_input={})
    try:
        await asyncio.wait_for(entered.wait(), 3)
        response = await client.post("/api/knowledge-library/market/publish", json={
            "artifact_id": built["sha256"], "title": "Selected notes", "client_request_id": str(uuid4()),
        })
        assert response.status_code == 200, response.text
        publisher["publisher_id"] = 42
    finally:
        release.set()
    result = await complete(service, response)
    assert result["state"] == "failed" and result["reason"] == "market_publisher_changed"
    record = service.jobs.records[result["job_id"]]
    with pytest.raises(market.LibraryError, match="market_publisher_changed"):
        await market.retry_market_job(service, host, record)
    assert record["frozen_input"]["publisher_id"] == 41
    assert uploaded == []


async def test_bridge_credentials_failure_leaves_publication_cancellable(scenario, monkeypatch):
    """No irreversible gate is crossed merely by asking for local credentials."""
    client, service, _host, built, _artifact, _share, actual = scenario
    transport(monkeypatch, actual, lambda _request: httpx.Response(500))
    original = market._credentials
    calls, cancelled = 0, []
    async def credentials(client):
        nonlocal calls
        calls += 1
        # Submission and worker author checks succeed; transport setup fails.
        if calls <= 2:
            return await original(client)
        job_id = next(iter(service.jobs.contexts))
        cancelled.append(await service.cancel_job(job_id))
        raise market.LibraryError("market_unavailable")
    monkeypatch.setattr(market, "_credentials", credentials)
    response = await client.post("/api/knowledge-library/market/publish", json={
        "artifact_id": built["sha256"], "title": "Selected notes", "client_request_id": str(uuid4()),
    })
    result = await complete(service, response)
    assert result["reason"] == "market_unavailable"
    assert cancelled[0]["cancel_requested"]


async def test_lost_publish_response_retry_keeps_original_author_and_uuid(scenario, monkeypatch):
    """An uncertain remote commit never authorizes retrying as another account."""
    client, service, host, built, _artifact, _share, actual = scenario
    publisher = {"publisher_id": 41, "market_api_url": "https://market.example.test"}
    uploads = []
    async def unavailable(request):
        uploads.append(json.loads((await request.aread()).split(b"\n", 1)[0]))
        raise httpx.ReadError("response was lost", request=request)
    transport(monkeypatch, actual, unavailable, publisher)
    request_id = str(uuid4())
    response = await client.post("/api/knowledge-library/market/publish", json={
        "artifact_id": built["sha256"], "title": "Notes", "client_request_id": request_id,
    })
    failed = await complete(service, response)
    assert failed["reason"] == "market_unavailable"
    publisher["publisher_id"] = 42
    with pytest.raises(market.LibraryError, match="market_publisher_changed"):
        await market.retry_market_job(service, host, service.jobs.records[failed["job_id"]])
    assert len(uploads) == 1 and uploads[0]["client_request_id"] == request_id


async def test_install_uses_frozen_share_and_new_service_without_reembedding(scenario, monkeypatch):
    client, service, host, _built, artifact, share, actual = scenario
    async def detail(*_args, **_kwargs):
        return {"ok": True, "share": share}
    monkeypatch.setattr(market, "_bridge_json", detail)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=artifact.read_bytes())
    transport(monkeypatch, actual, handler)
    response = await client.post("/api/knowledge-library/market/install", json={
        "share_id": share["id"], "revision": share["revision"], "file_sha256": share["file_sha256"],
    })
    result = await complete(service, response)
    assert result["state"] == "completed", result
    assert service.embedder.calls == []
    library = (await service.list_libraries())[0]
    assert library["kind"] == "installed"
    assert library["installation"]["artifact_sha256"] == share["file_sha256"]
    assert seen[0].url.params["revision"] == "3"
    assert seen[0].url.params["file_sha256"] == share["file_sha256"]
    assert not (service.store.root / "knowledge/registry.json").exists()
    assert list((host.root / "staging/market").glob("*")) == []


@pytest.mark.parametrize("failure", ["changed", "withdrawn", "corrupt"])
async def test_changed_nonpublished_or_corrupt_market_bytes_never_install(scenario, monkeypatch, failure):
    client, service, _host, _built, artifact, share, actual = scenario
    observed = {**share}
    if failure == "changed":
        observed["revision"] += 1
    if failure == "withdrawn":
        observed["status"] = "withdrawn"
    async def detail(*_args, **_kwargs):
        return {"ok": True, "share": observed}
    monkeypatch.setattr(market, "_bridge_json", detail)
    requests = []
    def handler(request):
        requests.append(request)
        raw = artifact.read_bytes()
        return httpx.Response(200, content=b"!" + raw[1:])
    transport(monkeypatch, actual, handler)
    response = await client.post("/api/knowledge-library/market/install", json={
        "share_id": share["id"], "revision": share["revision"], "file_sha256": share["file_sha256"],
    })
    result = await complete(service, response)
    assert result["state"] == "failed"
    assert await service.list_libraries() == []
    assert len(requests) == (1 if failure == "corrupt" else 0)
    assert list((service.store.root / "staging/market").glob("*")) == []


@pytest.mark.parametrize("cancellation", ["flag", "task"])
async def test_cancel_during_output_open_closes_handle_and_removes_stage(scenario, monkeypatch, cancellation):
    """Retain a just-opened handle even when its offloaded await is cancelled."""
    _client, service, host, _built, _artifact, share, _actual = scenario
    context = JobContext({"job_id": "a" * 32})
    entered, release, opened = asyncio.Event(), threading.Event(), []
    loop, original_open = asyncio.get_running_loop(), Path.open
    def open_file(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.parent == host.root / "staging/market":
            opened.append(handle)
            loop.call_soon_threadsafe(entered.set)
            if cancellation == "flag":
                context.cancelled.set()
            assert release.wait(3), "test did not release file opener"
        return handle
    monkeypatch.setattr(Path, "open", open_file)
    async def detail(*_args, **_kwargs):
        return {"ok": True, "share": share}
    monkeypatch.setattr(market, "_bridge_json", detail)
    task = asyncio.create_task(market._install_runner(service, host, {
        "share_id": share["id"], "revision": share["revision"], "file_sha256": share["file_sha256"],
    })(context))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if cancellation == "task":
            task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError if cancellation == "task" else market.LibraryError):
        await task
    assert len(opened) == 1 and opened[0].closed
    assert list((host.root / "staging/market").glob("*")) == []


async def test_duplicate_publish_post_recovers_persisted_job_after_lost_response(scenario, monkeypatch):
    """A browser without the first response can recover the same durable job."""
    client, service, host, built, _artifact, share, actual = scenario
    uploads = []
    async def remote(request):
        uploads.append(await request.aread())
        return httpx.Response(200, json={"ok": True, "share": share})
    transport(monkeypatch, actual, remote)
    payload = {"artifact_id": built["sha256"], "title": "Notes", "client_request_id": str(uuid4())}
    first = await client.post("/api/knowledge-library/market/publish", json=payload)
    original = await complete(service, first)
    await service.close()
    reopened = LibraryService(host.root, embedder=Encoder())
    await reopened.start()
    async def get_service():
        return reopened
    host.service = get_service
    try:
        recovered = await client.post("/api/knowledge-library/market/publish", json=payload)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["job"]["job_id"] == original["job_id"]
        assert len(await reopened.list_jobs()) == 1
        assert len(uploads) == 1
    finally:
        await reopened.close()


@pytest.mark.parametrize("changed", ["title", "artifact_id", "publisher_id"])
async def test_duplicate_publish_post_cannot_replace_frozen_payload_or_author(scenario, monkeypatch, changed):
    """The same UUID never authorizes a different work or publishing account."""
    client, service, _host, built, _artifact, _share, actual = scenario
    publisher = {"publisher_id": 41, "market_api_url": "https://market.example.test"}
    uploads = []
    async def remote(request):
        uploads.append(await request.aread())
        raise httpx.ReadError("response was lost", request=request)
    transport(monkeypatch, actual, remote, publisher)
    payload = {"artifact_id": built["sha256"], "title": "Notes", "client_request_id": str(uuid4())}
    first = await client.post("/api/knowledge-library/market/publish", json=payload)
    failed = await complete(service, first)
    assert failed["reason"] == "market_unavailable"
    if changed == "publisher_id":
        publisher["publisher_id"] = 42
    else:
        payload[changed] = "Another title" if changed == "title" else "f" * 64
    repeated = await client.post("/api/knowledge-library/market/publish", json=payload)
    assert repeated.status_code in {400, 409}, repeated.text
    assert repeated.json()["reason"] == ("market_publisher_changed" if changed == "publisher_id" else "market_request_conflict")
    assert len(await service.list_jobs()) == 1 and len(uploads) == 1


async def test_concurrent_identical_publish_posts_admit_one_job(scenario, monkeypatch):
    """Serialize identity lookup and durable admission for simultaneous retries."""
    client, service, _host, built, _artifact, share, actual = scenario
    uploads = []
    async def remote(request):
        uploads.append(await request.aread())
        return httpx.Response(200, json={"ok": True, "share": share})
    transport(monkeypatch, actual, remote)
    payload = {"artifact_id": built["sha256"], "title": "Notes", "client_request_id": str(uuid4())}
    responses = await asyncio.gather(*(client.post("/api/knowledge-library/market/publish", json=payload) for _ in range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["job"]["job_id"] == responses[1].json()["job"]["job_id"]
    await service.jobs.queue.join()
    assert len(await service.list_jobs()) == 1 and len(uploads) == 1


async def test_cancelled_http_admission_retains_lock_until_job_is_durable(scenario, monkeypatch):
    """A disconnected first POST cannot expose its unpersisted reservation."""
    client, service, host, built, _artifact, share, actual = scenario
    transport(monkeypatch, actual, lambda _request: httpx.Response(200, json={"ok": True, "share": share}))
    entered, release = asyncio.Event(), threading.Event()
    loop, original_save = asyncio.get_running_loop(), service.jobs._save
    first = True
    def save(record):
        nonlocal first
        if first and record["kind"] == "market_publish":
            first = False
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "test did not release job persistence"
        original_save(record)
    monkeypatch.setattr(service.jobs, "_save", save)
    payload = {"artifact_id": built["sha256"], "title": "Notes", "client_request_id": str(uuid4())}
    original = asyncio.create_task(client.post("/api/knowledge-library/market/publish", json=payload))
    repeated = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        original.cancel()
        with pytest.raises(asyncio.CancelledError):
            await original
        repeated = asyncio.create_task(client.post("/api/knowledge-library/market/publish", json=payload))
        done, _ = await asyncio.wait({repeated}, timeout=0.05)
        assert not done, "duplicate request returned before the original durable admission completed"
    finally:
        release.set()
        if repeated is not None:
            await repeated
        await asyncio.gather(*tuple(getattr(host, "_market_publication_tasks", ())), return_exceptions=True)
    assert repeated.result().status_code == 200
    await service.jobs.queue.join()
    assert len(await service.list_jobs()) == 1


async def test_waiting_publication_admissions_are_bounded(scenario, monkeypatch):
    """The admission lock must not introduce an unbounded second task queue."""
    client, service, _host, built, _artifact, share, actual = scenario
    service.limits = replace(service.limits, max_pending_jobs=1)
    transport(monkeypatch, actual, lambda _request: httpx.Response(200, json={"ok": True, "share": share}))
    entered, release = asyncio.Event(), asyncio.Event()
    async def publisher():
        entered.set()
        await release.wait()
        return {"publisher_id": 41, "market_api_url": "https://market.example.test"}
    monkeypatch.setattr(market, "_publisher", publisher)
    payload = {"artifact_id": built["sha256"], "title": "Notes", "client_request_id": str(uuid4())}
    first = asyncio.create_task(client.post("/api/knowledge-library/market/publish", json=payload))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(client.post("/api/knowledge-library/market/publish", json=payload))
        done, _ = await asyncio.wait({second}, timeout=0.05)
        assert done, "excess admission waited behind the lock instead of being rejected"
        assert second.result().status_code == 429
        assert second.result().json()["reason"] == "jobs_busy"
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []))
    await service.jobs.queue.join()


async def test_main_guard_rejects_before_body_parse_or_any_market_operation(scenario, monkeypatch):
    client, service, *_rest = scenario
    monkeypatch.setattr(local, "_validate_local_mutation_request", lambda *_args, **_kwargs: JSONResponse(
        {"ok": False, "reason": "csrf_validation_failed"}, status_code=403,
    ))
    response = await client.post("/api/knowledge-library/market/publish", content=b"not even JSON")
    assert response.status_code == 403
    assert await service.list_jobs() == []

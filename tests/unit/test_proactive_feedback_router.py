"""Local authorization, lifecycle and durable-store transport integration."""
import asyncio
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_logic.recommendation_feedback import RecommendationStore
from main_routers import proactive_feedback_router as routes
from main_routers.system_router import _shared
from services.chat_sync import host as credentials
from services.proactive_feedback import host
from services.proactive_feedback.protocol import COMMUNITY_ORIGIN
from services.proactive_feedback.runtime import ProactiveFeedbackRuntime, SyncFailure, drain_settings
from tests.unit.test_proactive_feedback_runtime import receipt


def test_host_reads_snapshot_under_credential_writer_fence(monkeypatch):
    active = []
    snapshot = {"access_token": "s", "local_user_id": "a", "base_url": COMMUNITY_ORIGIN}

    @contextmanager
    def fence():
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    def read():
        assert active
        return snapshot

    monkeypatch.setattr(credentials, "saved_session_writer_fence", fence)
    monkeypatch.setattr(credentials, "saved_session_snapshot", read)
    assert host.saved_snapshot() == snapshot
    assert host.saved_snapshot() is not snapshot
    with host.attribution_context() as owner:
        assert owner["user_id"] == "a" and active
    monkeypatch.setattr(credentials, "saved_session_snapshot", lambda: None)
    assert host.saved_snapshot() is None


@pytest.mark.asyncio
async def test_host_uses_shared_oauth_singleflight_resolver(monkeypatch):
    async def resolve():
        return {"logged_in": True}
    monkeypatch.setattr(credentials, "resolve_saved_oauth_status", resolve)
    assert await host.resolve_session() == {"logged_in": True}


@pytest.mark.asyncio
async def test_real_sqlite_settings_and_behavior_batch_remains_exact_after_restart(tmp_path):
    snapshot = {"access_token": "test-only", "local_user_id": "user-a", "base_url": COMMUNITY_ORIGIN}
    owner = host.owner_from_snapshot(snapshot)
    path = tmp_path / "feedback.sqlite3"
    store = RecommendationStore(path)
    rec = await asyncio.to_thread(store.create, owner, "Neko", "turn-test", {"name": "Song", "artist": "Artist", "url": "https://music.example/a"}, {"proactiveChatEnabled": True})
    await asyncio.to_thread(store.record, owner, rec["id"], {"event_id": "shown-1", "kind": "shown"})
    await asyncio.to_thread(store.enqueue_settings, owner, "settings-1", {"revision": 1, "origin": "user", "occurred_at": 100.0,
                                                                          "previous": {"proactiveMusicEnabled": False},
                                                                          "settings": {"proactiveMusicEnabled": True}})
    bodies = []

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        bodies.append(request.content)
        if len(bodies) == 1:
            raise httpx.ReadError("ack lost", request=request)
        return httpx.Response(200, json=receipt(json.loads(request.content), "duplicate"))

    async def resolver():
        return {"logged_in": True, "snapshot": dict(snapshot)}

    @contextmanager
    def attribution():
        yield owner

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = ProactiveFeedbackRuntime(store, client=client, snapshot_provider=lambda: snapshot,
                                         session_provider=resolver, attribution_context=attribution)
        assert (await first.sync_once())["state"] == "retry_wait"
        await first.close()
        second = ProactiveFeedbackRuntime(RecommendationStore(path), client=client, snapshot_provider=lambda: snapshot,
                                          session_provider=resolver, attribution_context=attribution)
        try:
            assert (await second.sync_once())["state"] == "synced"
            assert bodies[0] == bodies[1]
            status = await second.status()
            assert status["acked"] == 3 and status["pending"] == 0
            assert {item["kind"] for item in json.loads(bodies[0])["items"]} == {"recommendation", "event", "settings"}
        finally:
            await second.close()


def test_local_status_and_sync_require_origin_csrf_and_redact_failures(monkeypatch):
    calls = []

    async def status():
        calls.append("status")
        return {"pending": 2}

    async def sync():
        calls.append("sync")
        raise SyncFailure("private exception detail")

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(status=status, sync_once=sync))
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf")
    app = FastAPI()
    app.include_router(routes.router)
    # Avoid lifespan here: this test deliberately installs a simulated runtime.
    client = TestClient(app, base_url="http://127.0.0.1:48911")
    assert client.get("/api/proactive/feedback/status").status_code == 403
    assert client.post("/api/proactive/feedback/sync").status_code == 403
    headers = {"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test-csrf"}
    response = client.get("/api/proactive/feedback/status", headers=headers)
    assert response.status_code == 200 and response.json()["pending"] == 2
    assert response.headers["cache-control"] == "no-store"
    response = client.post("/api/proactive/feedback/sync", headers=headers)
    assert response.status_code == 503 and "private" not in response.text
    monkeypatch.setattr(routes, "_runtime", None)
    assert client.get("/api/proactive/feedback/status", headers=headers).json()["error_code"] == "feedback_not_started"
    assert calls == ["status", "sync"]


@pytest.mark.asyncio
async def test_startup_waits_for_storage_gate_and_shutdown_owns_worker(monkeypatch):
    from utils import preferences
    installed, created, started, closed = [], [], [], []

    async def close():
        closed.append(True)

    async def factory():
        created.append(True)
        return SimpleNamespace(start=lambda: started.append(True), close=close)

    monkeypatch.setattr(preferences, "install_settings_attribution_context", lambda factory: installed.append(factory))
    monkeypatch.setattr(routes, "create_runtime", factory)
    monkeypatch.setattr(routes, "_runtime", None)
    monkeypatch.setattr(routes, "_bootstrap_task", None)
    monkeypatch.setattr(routes, "_closing", False)
    await routes.start_runtime()
    assert installed == [host.attribution_context]
    assert not created  # Local startup must not pin pre-selection storage.
    routes.schedule_start()
    routes.schedule_start()
    await routes._bootstrap_task
    routes.schedule_start()
    assert len(created) == len(started) == 1
    await routes.close_runtime()
    assert closed == [True] and installed[-1] is None
    routes.schedule_start()
    assert routes._bootstrap_task is None


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_bootstrap_and_prevents_late_start(monkeypatch):
    from utils import preferences
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def factory():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(preferences, "install_settings_attribution_context", lambda factory: None)
    monkeypatch.setattr(routes, "create_runtime", factory)
    monkeypatch.setattr(routes, "_runtime", None)
    monkeypatch.setattr(routes, "_bootstrap_task", None)
    monkeypatch.setattr(routes, "_closing", False)
    routes.schedule_start()
    await entered.wait()
    await asyncio.wait_for(routes.close_runtime(), 3)
    assert cancelled.is_set() and routes._runtime is None


@pytest.mark.asyncio
async def test_optional_bootstrap_failure_does_not_break_application(monkeypatch):
    from utils import preferences

    async def factory():
        raise OSError("sensitive path")

    monkeypatch.setattr(preferences, "install_settings_attribution_context", lambda factory: None)
    monkeypatch.setattr(routes, "create_runtime", factory)
    monkeypatch.setattr(routes, "_runtime", None)
    monkeypatch.setattr(routes, "_bootstrap_task", None)
    monkeypatch.setattr(routes, "_closing", False)
    routes.schedule_start()
    await routes._bootstrap_task
    assert routes._runtime is None
    await routes.close_runtime()


@pytest.mark.asyncio
async def test_default_factory_owns_http_client_with_no_environment_proxy(tmp_path, monkeypatch):
    from main_routers.proactive_feedback_router import create_runtime
    from utils import config_manager
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(get_runtime_config_path=lambda name: tmp_path / name))
    runtime = await create_runtime()
    assert runtime.http.trust_env is False
    assert runtime.http.follow_redirects is False
    assert runtime.store.path == tmp_path / "recommendation_feedback.sqlite3"
    await runtime.close()
    assert runtime.http.is_closed


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
@pytest.mark.asyncio
async def test_factory_honors_operator_upload_pause_at_startup(tmp_path, monkeypatch, value):
    from main_routers.proactive_feedback_router import create_runtime
    from utils import config_manager

    monkeypatch.setenv("NEKO_PROACTIVE_FEEDBACK_UPLOAD_PAUSED", value)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(get_runtime_config_path=lambda name: tmp_path / name))
    runtime = await create_runtime()
    try:
        assert runtime.upload_paused is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_full_sqlite_outbox_uploads_before_retrying_settings_journal(tmp_path, monkeypatch):
    from utils import preferences
    snapshot = {"access_token": "test-only", "local_user_id": "user-a", "base_url": COMMUNITY_ORIGIN}
    owner = host.owner_from_snapshot(snapshot)
    store = RecommendationStore(tmp_path / "full.sqlite3")
    store.MAX_PENDING_UPLOADS = 1
    await asyncio.to_thread(store.create, owner, "Neko", "turn", {"name": "Song", "artist": "Artist", "url": "https://music.example/a"}, {})
    journal = [{"id": "settings-1", "owner": owner, "payload": {"revision": 1, "origin": "user", "occurred_at": 100.0,
                                                                   "previous": {"proactiveMusicEnabled": False},
                                                                   "settings": {"proactiveMusicEnabled": True}}}]
    monkeypatch.setattr(preferences, "pending_settings_changes", lambda limit: list(journal))
    monkeypatch.setattr(preferences, "acknowledge_settings_changes", lambda ids: journal.clear())

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        return httpx.Response(200, json=receipt(json.loads(request.content)))

    async def resolver():
        return {"logged_in": True, "snapshot": snapshot}

    @contextmanager
    def attribution():
        yield owner

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProactiveFeedbackRuntime(store, client=client, snapshot_provider=lambda: snapshot, session_provider=resolver,
                                           attribution_context=attribution, settings_drain=drain_settings)
        try:
            assert (await runtime.sync_once())["state"] == "synced"
            assert journal  # Failed import was not acknowledged/deleted.
            status = await runtime.status()
            assert status["acked"] == 1 and status["pending"] == 0
            assert status["settings_import_error"] == "settings_import_unavailable"
            assert (await runtime.sync_once())["state"] == "synced"
            assert not journal
            status = await runtime.status()
            assert status["acked"] == 2 and status["settings_import_error"] == ""
        finally:
            await runtime.close()


@pytest.mark.parametrize("payload", [b"[" * 1000 + b"]" * 1000, b'{"x":' * 300 + b"0" + b"}" * 300],
                         ids=["nested-array", "nested-object"])
def test_resume_deep_json_is_validation_error_without_calling_runtime(monkeypatch, payload):
    calls = []

    async def resume(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Malformed command must never reach recovery")

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(resume_blocked_batch=resume))
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf")
    app = FastAPI()
    app.include_router(routes.router)
    # Avoid lifespan because this isolated route test installs a synthetic runtime.
    client = TestClient(app, base_url="http://127.0.0.1:48911", raise_server_exceptions=False)
    try:
        response = client.post("/api/proactive/feedback/resume", content=payload,
                               headers={"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test-csrf",
                                        "Content-Type": "application/json"})
    finally:
        client.close()
    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_feedback_resume"
    assert not calls


def test_resume_route_requires_origin_csrf_exact_command_and_redacts_errors(monkeypatch):
    command = {"owner_key": "a" * 64, "batch_id": str(uuid.uuid4()), "batch_hash": "b" * 64}
    calls = []

    async def resume(**kwargs):
        calls.append(kwargs)
        return {"state": "resumed", "batch_id": kwargs["batch_id"]}

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(resume_blocked_batch=resume))
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf")
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, base_url="http://127.0.0.1:48911")
    path = "/api/proactive/feedback/resume"
    headers = {"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test-csrf"}
    assert client.post(path, json=command).status_code == 403
    assert client.post(path, json=command, headers={**headers, "Origin": "https://example.invalid"}).status_code == 403
    for invalid in ({}, {**command, "batch_hash": "wrong"}, {**command, "owner_key": 1}, {**command, "payload": "PRIVATE"}):
        response = client.post(path, json=invalid, headers=headers)
        assert response.status_code == 422 and "PRIVATE" not in response.text
    assert client.post(path, content="x", headers=headers).status_code == 415
    assert client.post(path, content="x" * 2049, headers={**headers, "Content-Type": "application/json"}).status_code == 413
    response = client.post(path, json=command, headers=headers)
    assert response.status_code == 200 and response.json()["state"] == "resumed"
    assert response.headers["cache-control"] == "no-store"
    assert calls == [command]

    async def conflict(**kwargs):
        from main_logic.recommendation_feedback import FeedbackConflict
        raise FeedbackConflict("private conflict detail")

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(resume_blocked_batch=conflict))
    response = client.post(path, json=command, headers=headers)
    assert response.status_code == 409 and "private" not in response.text

    async def replaced(**kwargs):
        raise SyncFailure("session_changed")

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(resume_blocked_batch=replaced))
    assert client.post(path, json=command, headers=headers).json()["error_code"] == "session_changed"

    async def unavailable(**kwargs):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(routes, "_runtime", SimpleNamespace(resume_blocked_batch=unavailable))
    response = client.post(path, json=command, headers=headers)
    assert response.status_code == 503 and "private" not in response.text
    monkeypatch.setattr(routes, "_runtime", None)
    assert client.post(path, json=command, headers=headers).status_code == 503


@pytest.mark.parametrize("rejected_status", [409, 422])
@pytest.mark.asyncio
async def test_http_resume_actual_sqlite_keeps_original_batch_and_receipts(tmp_path, monkeypatch, rejected_status):
    from services.proactive_feedback.protocol import payload_hash
    snapshot = {"access_token": "test-only", "local_user_id": "user-a", "base_url": COMMUNITY_ORIGIN}
    owner = host.owner_from_snapshot(snapshot)
    store = RecommendationStore(tmp_path / "resume.sqlite3")
    rec = await asyncio.to_thread(store.create, owner, "Neko", "turn", {"name": "Song", "artist": "Artist", "url": "https://music.example/a"}, {})
    repaired, bodies = False, []

    async def cloud(request):
        if request.method == "GET":
            return httpx.Response(200, json={"user": {"id": "user-a"}})
        bodies.append(request.content)
        return (httpx.Response(200, json=receipt(json.loads(request.content))) if repaired
                else httpx.Response(rejected_status))

    async def resolve():
        return {"logged_in": True, "snapshot": dict(snapshot)}

    @contextmanager
    def attribution():
        yield host.owner_from_snapshot(snapshot)

    async with httpx.AsyncClient(transport=httpx.MockTransport(cloud)) as cloud_client:
        runtime = ProactiveFeedbackRuntime(store, client=cloud_client, snapshot_provider=lambda: dict(snapshot),
                                           fenced_snapshot_provider=lambda: dict(snapshot), session_provider=resolve,
                                           attribution_context=attribution)
        monkeypatch.setattr(routes, "_runtime", runtime)
        monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf")
        app = FastAPI()
        app.include_router(routes.router)
        headers = {"Origin": "http://127.0.0.1:48911", "X-CSRF-Token": "test-csrf"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=headers["Origin"], headers=headers) as local:
            try:
                assert (await runtime.sync_once())["state"] == "blocked"
                status = (await local.get("/api/proactive/feedback/status")).json()
                assert status["blocked"] and status["batch_hash"] == payload_hash(json.loads(bodies[0]))
                command = {key: status[key] for key in ("owner_key", "batch_id", "batch_hash")}
                response = await local.post("/api/proactive/feedback/resume", json={**command, "batch_hash": "0" * 64})
                assert response.status_code == 409 and (await runtime.status())["blocked"]
                snapshot["local_user_id"] = "user-b"
                assert (await local.post("/api/proactive/feedback/resume", json=command)).status_code == 409
                snapshot["local_user_id"] = "user-a"
                repaired = True
                assert (await runtime.sync_once())["state"] == "blocked"
                response = await local.post("/api/proactive/feedback/resume", json=command)
                assert response.status_code == 200 and response.json()["state"] == "resumed"
                assert (await runtime.sync_once())["state"] == "synced"
                assert bodies[0] == bodies[1]
                status = await runtime.status()
                assert status["fully_synced"] and status["acked"] == 1
                assert status["last_acknowledged_batch"]["batch_id"] == command["batch_id"]
                assert status["last_acknowledged_batch"]["batch_hash"] == command["batch_hash"]
                assert (await local.post("/api/proactive/feedback/resume", json=command)).status_code == 409
                assert store.prepare_batch(owner) is None and rec["id"] == json.loads(bodies[0])["items"][0]["id"]
            finally:
                await runtime.close()

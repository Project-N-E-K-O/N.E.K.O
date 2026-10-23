"""Real local Store with the control-first worker and a synthetic HTTP peer."""
import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
import httpx
import pytest

from main_logic.recommendation_feedback import RecommendationStore
from services.proactive_feedback import host
from services.proactive_feedback import capture
from services.proactive_feedback.protocol import fact_hash_v2, payload_hash
from services.proactive_feedback.runtime_v2 import ProactiveFeedbackRuntimeV2
from services.proactive_feedback.runtime import SyncFailure


@pytest.fixture
def env(tmp_path):
    saved = {"base_url": "https://community.example", "local_user_id": str(uuid.uuid4()),
             "access_token": "synthetic-token"}
    owner = host.owner_from_snapshot(saved)
    store = RecommendationStore(tmp_path / "local.sqlite3", protocol_version=2)
    installation = store.storage_incarnation(owner)
    registration = str(uuid.uuid4())
    state = {"account_incarnation": str(uuid.uuid4()), "clear_generation": 0,
             "registrations": {registration: {"installation_id": installation, "active": True, "version": 1}}}
    requests, uploads = [], []
    options = {"identity": saved["local_user_id"], "lose_response": False, "unauthorized": False, "upgrade": False}

    async def handler(request):
        requests.append(request.url.path)
        if options["unauthorized"]:
            return httpx.Response(401)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={"user": {"id": options["identity"]}})
        if options["upgrade"]:
            return httpx.Response(426, json={"detail": "proactive_upgrade_required"})
        if request.url.path.endswith("/devices"):
            return httpx.Response(200, json={"account_incarnation": state["account_incarnation"], "clear_generation": state["clear_generation"],
                "max_active_devices": 5, "devices": [{"registration_id": key, "registration_version": row["version"],
                    "installation_id": row["installation_id"], "active": row["active"], "display_name": row.get("display_name")}
                    for key, row in state["registrations"].items()]})
        if request.url.path.endswith("/devices/activate"):
            command = json.loads(request.content)
            active = [row for row in state["registrations"].values() if row["active"]]
            replace = command.get("replace_registration_id")
            if len(active) >= 5 and replace is None:
                return httpx.Response(409, json={"detail": "device_limit"})
            if replace:
                row = state["registrations"][replace]
                row.update(active=False, version=row["version"] + 1)
            previous = state["registrations"].get(registration, {"version": 0})
            state["registrations"][registration] = {"installation_id": command["installation_id"], "active": True,
                "version": previous["version"] + 1, "display_name": command.get("display_name")}
            return httpx.Response(200, json={"binding": {"account_incarnation": state["account_incarnation"],
                "clear_generation": state["clear_generation"], "registration_id": registration,
                "registration_version": state["registrations"][registration]["version"]}})
        if request.url.path.endswith("/remove"):
            key = request.url.path.split('/')[-2]
            row, command = state["registrations"][key], json.loads(request.content)
            if command["expected_registration_version"] != row["version"]:
                return httpx.Response(409, json={"detail": "device_version"})
            row.update(active=False, version=row["version"] + 1)
            return httpx.Response(200, json={"removed": True, "registration_id": key, "registration_version": row["version"]})
        if request.url.path.endswith("/control"):
            return httpx.Response(200, json=copy.deepcopy(state))
        if request.url.path.endswith("/anchors"):
            return httpx.Response(200, json={"anchor_id": str(uuid.uuid4()), "anchor_token": "synthetic-anchor"})
        if request.url.path.endswith("/batches"):
            batch = json.loads(request.content)
            uploads.append(batch)
            if options["lose_response"]:
                options["lose_response"] = False
                raise httpx.ReadError("synthetic reply loss", request=request)
            receipt = {key: batch[key] for key in ("schema_version", "batch_id", "source_id", "binding")}
            receipt.update(batch_hash=payload_hash(batch), results=[{
                "id": item["id"], "kind": item["kind"],
                "payload_hash": fact_hash_v2(item, batch["binding"]), "status": options.get("defer") or "stored"
            } for item in batch["items"]])
            return httpx.Response(200, json=receipt)
        raise AssertionError(request.url.path)

    async def resolve():
        return {"logged_in": True, "snapshot": dict(saved)}

    @contextmanager
    def attribution():
        yield host.owner_from_snapshot(saved)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runtime = ProactiveFeedbackRuntimeV2(store, snapshot_provider=lambda: dict(saved),
        session_provider=resolve, client=client, attribution_context=attribution,
        fenced_snapshot_provider=lambda: dict(saved), trusted_origin=saved["base_url"])
    return SimpleNamespace(saved=saved, owner=owner, store=store, runtime=runtime, client=client,
        state=state, registration=registration, requests=requests, uploads=uploads, options=options)


def shown(env):
    context = env.runtime.control_snapshot(env.owner)
    card = env.store.create(env.owner, "Fixture", "fixture-turn",
        {"name": "Fixture", "artist": "Fixture", "url": "https://music.example/fixture"}, {}, control=context)
    event = {"event_id": str(uuid.uuid4()), "kind": "shown", "occurred_at": 100,
             "context": env.runtime.control_snapshot(env.owner), "offer_context": card["offer_context"]}
    result = env.store.record(env.owner, card["id"], event, control=context)
    assert result["telemetry"]["status"] == "durable"
    return card, event


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0, -1, True, "1"])
async def test_invalid_registration_version_is_retryable_without_killing_worker(env, version):
    try:
        row = env.state["registrations"][env.registration]
        if version is None:
            del row["version"]
        else:
            row["version"] = version
        result = await env.runtime.sync_once()
        assert result == {"state": "retry_wait", "error_code": "invalid_control"}
        assert env.store.load_control(env.owner) is None
        assert env.runtime.control_snapshot(env.owner) is None
        row["version"] = 1
        assert (await env.runtime.sync_once())["state"] == "idle"
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["time_deferred", "dependency_deferred"])
async def test_deferred_facts_retry_original_capture_after_normal_facts_without_rebinding(env, monkeypatch, reason):
    import time
    from main_logic import recommendation_feedback as module
    try:
        await env.runtime.sync_once()
        _, original = shown(env)
        env.options["defer"] = reason
        assert (await env.runtime.sync_once())["state"] == "synced"
        first = copy.deepcopy(env.uploads[-1])
        assert env.store.sync_status(env.owner)["held"] == 1
        env.options["defer"] = None
        shown(env)
        assert (await env.runtime.sync_once())["state"] == "synced"
        assert env.uploads[-1]["items"][0]["id"] != original["event_id"]
        # Advance this module's retry scheduler, never the shared capture clock.
        later = time.time() + 61
        monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: later))
        assert (await env.runtime.sync_once())["state"] == "synced"
        retried = env.uploads[-1]
        assert retried["batch_id"] != first["batch_id"]
        assert retried["binding"] == first["binding"]
        assert retried["items"] == first["items"]
        assert env.store.sync_status(env.owner)["pending"] == 0
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_empty_queue_still_refreshes_control_and_renderer_gets_its_own_clock(env):
    try:
        assert (await env.runtime.sync_once())["state"] == "idle"
        assert env.requests == ["/api/users/me", "/api/proactive/feedback/v2/control", "/api/proactive/feedback/v2/anchors"]
        context = await env.runtime.capture_context(str(uuid.uuid4()))
        assert context["owner_key"] == env.owner["key"]
        assert context["binding_revision"] == 1
        assert "anchor_token" in context
        assert not (await env.runtime.status())["fully_synced"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_removed_and_unknown_devices_never_auto_activate(env):
    try:
        env.state["registrations"].clear()
        assert (await env.runtime.sync_once())["state"] == "device_activation_required"
        env.state["registrations"][env.registration] = {
            "installation_id": env.store.storage_incarnation(env.owner), "active": False, "version": 2}
        assert (await env.runtime.sync_once())["state"] == "registration_removed"
        assert not any("activate" in path or "batches" in path for path in env.requests)
        assert env.runtime.control_snapshot(env.owner) is None
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_oauth_identity_mismatch_cannot_discover_or_write_other_account_control(env):
    try:
        env.options["identity"] = str(uuid.uuid4())
        result = await env.runtime.sync_once()
        assert result["error_code"] == "account_mismatch"
        assert env.requests == ["/api/users/me"]
        assert env.store.load_control(env.owner) is None
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_real_local_outbox_retries_same_frozen_body_and_only_matching_receipt_acks(env):
    try:
        await env.runtime.sync_once()
        shown(env)
        env.options["lose_response"] = True
        assert (await env.runtime.sync_once())["state"] == "retry_wait"
        assert env.store.sync_status(env.owner)["pending"] == 1
        assert (await env.runtime.sync_once())["state"] == "synced"
        assert env.uploads[0] == env.uploads[1]
        status = await env.runtime.status()
        assert status["local_queue_empty"] and status["fully_synced"]
        assert "synthetic-token" not in json.dumps(status)
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_generation_change_before_upload_discards_backlog_without_rebinding(env):
    try:
        await env.runtime.sync_once()
        _, event = shown(env)
        old = copy.deepcopy(event)
        env.store.prepare_batch(env.owner)
        env.state["clear_generation"] = 1
        assert (await env.runtime.sync_once())["state"] == "idle"
        assert not env.uploads
        assert env.store.sync_status(env.owner)["pending"] == 0
        assert event == old
        assert env.runtime.control_snapshot(env.owner)["clear_generation"] == 1
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_paused_upload_still_reads_new_control_and_invalidates_old_capture(env):
    try:
        await env.runtime.sync_once()
        shown(env)
        env.runtime.upload_paused = True
        env.state["clear_generation"] = 1
        assert (await env.runtime.sync_once())["state"] == "paused"
        assert env.store.sync_status(env.owner)["pending"] == 0
        assert not env.uploads
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_capture_auth_failure_cannot_keep_old_success_status_or_context(env):
    try:
        await env.runtime.sync_once()
        shown(env)
        await env.runtime.sync_once()
        assert (await env.runtime.status())["fully_synced"]
        env.options["unauthorized"] = True
        with pytest.raises(SyncFailure, match="authentication_required"):
            await env.runtime.capture_context(str(uuid.uuid4()))
        status = await env.runtime.status()
        assert not status["fully_synced"] and not status["identity_verified"]
        assert env.runtime.control_snapshot(env.owner) is None
    finally:
        await env.client.aclose()


def test_capture_provider_filters_account_and_copies_original_context():
    original = {"owner_key": "a", "capture": {"tick_ms": 10}}
    try:
        capture.install_control_provider(lambda _: original)
        assert capture.get_control_snapshot({"key": "b"}) is None
        value = capture.get_control_snapshot({"key": "a"})
        value["capture"]["tick_ms"] = 100
        assert original["capture"]["tick_ms"] == 10
    finally:
        capture.install_control_provider(None)
    assert capture.get_control_snapshot({"key": "a"}) is None


@pytest.mark.asyncio
async def test_actual_local_capture_route_validates_body_before_requesting_cloud(env, monkeypatch):
    from main_routers import proactive_feedback_router as routes
    monkeypatch.setattr(routes, "_runtime", env.runtime)
    monkeypatch.setattr(routes, "_validate", lambda _: None)
    app = FastAPI()
    app.include_router(routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            path = "/api/proactive/feedback/capture-context"
            assert (await client.post(path, content="{}", headers={"Content-Type": "text/plain"})).status_code == 415
            assert (await client.post(path, json={"clock_session_id": "invalid"})).status_code == 422
            assert not env.requests
            response = await client.post(path, json={"clock_session_id": str(uuid.uuid4())})
            assert response.status_code == 200
            assert response.json()["context"]["owner_key"] == env.owner["key"]
            assert response.headers["Cache-Control"] == "no-store"
            env.options["unauthorized"] = True
            assert (await client.post(path, json={"clock_session_id": str(uuid.uuid4())})).status_code == 503
            assert env.runtime.control_snapshot(env.owner) is None
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_explicit_activation_auth_failure_revokes_previous_verified_state(env):
    try:
        await env.runtime.sync_once()
        env.options["unauthorized"] = True
        with pytest.raises(SyncFailure, match="authentication_required"):
            await env.runtime.activate_device()
        assert env.runtime.control_snapshot(env.owner) is None
        assert not (await env.runtime.status())["identity_verified"]
    finally:
        await env.client.aclose()


@pytest.mark.asyncio
async def test_settings_io_failure_keeps_queued_facts_uploadable(env):
    try:
        await env.runtime.sync_once()
        shown(env)
        def fail(_):
            raise OSError("synthetic journal unavailable")
        env.runtime.settings_drain = fail
        assert (await env.runtime.sync_once())["state"] == "synced"
        status = await env.runtime.status()
        assert not status["fully_synced"]
        assert status["settings_import_error"] == "settings_import_unavailable"
    finally:
        await env.client.aclose()

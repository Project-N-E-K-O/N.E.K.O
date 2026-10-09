"""Exercise real recommendation routes/store and settings-writer ownership."""

import asyncio
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from config.topic_recommendation_settings import TopicRecommendationSettings
from main_logic.topic.recommendation import registry
from main_logic.topic.recommendation.service import TopicRecommendationService
from main_logic.topic.recommendation.store import RecommendationStore
import main_logic.topic.recommendation.store as store_module
from main_routers import proactive_router, recommendation_controls, shared_state
from main_routers.system_router import _shared as security
from utils.preferences import ConversationSettingsSnapshot

CAT = "character_" + "a" * 32
OTHER = "character_" + "b" * 32
HEADERS = {"Origin": "http://testserver", "X-CSRF-Token": "recommendation-test-token"}


@pytest.mark.asyncio
async def test_recovery_preserves_publication_but_rejects_new_input_during_flush(owner, monkeypatch):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    # Keep publication queued until the real recovery transaction owns its flush.
    # Otherwise the background worker can consume this barrier before reset begins.
    monkeypatch.setattr(owner.service, "_wake", lambda slot: slot.changed.set())
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    snapshot = owner.service.snapshot(CAT)
    assert owner.service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"],
                                             "confirmed-publication", "How is the painting?")
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    entered, release = threading.Event(), threading.Event()
    original_replace = owner.store._replace

    def delayed_publication_commit(*args, **kwargs):
        result = original_replace(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(owner.store, "_replace", delayed_publication_commit)
    task = asyncio.create_task(owner.client.post("/api/proactive/recommendation/recover", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "flush-confirmed",
              "expected_reset_generation": proof["reset_generation"], "expected_confirmation": proof["reset_confirmation"]}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        sink.note_turn(turn("New update during publication flush", turn_id="during-flush"))
        release.set()
        result = await task
        assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"
        current = await owner.store.load(CAT)
        assert current["state_epoch"] == proof["epoch"] and current["revision"] == proof["revision"] + 1
        assert current["deliveries"][0]["delivery_id"] == "confirmed-publication"
        assert not current["reset_requests"]
        slot = owner.service._characters[CAT]
        assert not slot.captures and [event.turn_id for event in slot.events] == ["during-flush"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reset", "recover"])
@pytest.mark.parametrize("analyze_new_turn", [False, True])
async def test_confirmation_cannot_include_input_arriving_during_owner_lookup(owner, monkeypatch, mode, analyze_new_turn):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "reviewed-operation",
            "expected_reset_generation": proof["reset_generation"], "expected_confirmation": proof["reset_confirmation"]}
    entered, release = threading.Event(), threading.Event()
    def delayed_owner(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"猫娘": {"Yui": {"_reserved": {"character_id": CAT}}}}
    monkeypatch.setattr(owner.config, "load_characters", delayed_owner)
    task = asyncio.create_task(owner.client.post(f"/api/proactive/recommendation/{mode}", json=body, headers=HEADERS))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        sink.note_turn(turn("A new painting update", turn_id="after-confirm"))
        if analyze_new_turn:
            await owner.service.process_pending(CAT)
        updated = await owner.store.load(CAT)
        release.set()
        result = await task
        assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"
        assert await owner.store.load(CAT) == updated
        if analyze_new_turn:
            assert "after-confirm" in updated["subjects"][0]["evidence_turn_ids"]
        else:
            assert [e.turn_id for e in owner.service._characters[CAT].events] == ["after-confirm"]
        fresh = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
        retried = await owner.client.post(f"/api/proactive/recommendation/{mode}", headers=HEADERS,
            json={**body, "expected_confirmation": fresh["reset_confirmation"], "request_id": "fresh-reviewed-operation"})
        assert retried.status_code == 200
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reset", "recover"])
async def test_confirmation_is_rechecked_at_physical_replace(owner, monkeypatch, mode):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    before = await owner.store.load(CAT)
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    entered, release = threading.Event(), threading.Event()
    original_fsync = store_module.os.fsync
    def delayed_fsync(descriptor):
        result = original_fsync(descriptor)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(store_module.os, "fsync", delayed_fsync)
    task = asyncio.create_task(owner.client.post(f"/api/proactive/recommendation/{mode}", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "late-input",
              "expected_reset_generation": proof["reset_generation"], "expected_confirmation": proof["reset_confirmation"]}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        sink.note_turn(turn("Input while physical operation waits", turn_id="after-physical-prepare"))
        release.set()
        result = await task
        assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"
        assert await owner.store.load(CAT) == before
        assert [e.turn_id for e in owner.service._characters[CAT].events] == ["after-physical-prepare"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_new_publication_invalidates_confirmation_without_erasing_its_receipt(owner):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    snapshot = owner.service.snapshot(CAT)
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    assert owner.service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "new-publication", "How is the painting?")
    response = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "before-publication",
              "expected_reset_generation": proof["reset_generation"], "expected_confirmation": proof["reset_confirmation"]})
    assert response.status_code == 409
    await owner.service.flush_publications()
    assert (await owner.store.load(CAT))["deliveries"][0]["delivery_id"] == "new-publication"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reset", "recover"])
async def test_confirmed_physical_commit_retry_preserves_later_input(owner, monkeypatch, mode):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "uncertain-confirmed",
            "expected_reset_generation": proof["reset_generation"], "expected_confirmation": proof["reset_confirmation"]}
    entered, release = threading.Event(), threading.Event()
    original_replace = owner.store._replace
    def delayed_replace(*args, **kwargs):
        result = original_replace(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(owner.store, "_replace", delayed_replace)
    task = asyncio.create_task(owner.client.post(f"/api/proactive/recommendation/{mode}", json=body, headers=HEADERS))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        committed = await owner.store.load(CAT)
        assert committed["state_epoch"] != proof["epoch"]
        monkeypatch.setattr(owner.store, "_replace", original_replace)
        sink.note_turn(turn("My new painting task", turn_id="after-confirmed-commit"))
        retry = await owner.client.post(f"/api/proactive/recommendation/{mode}", json=body, headers=HEADERS)
        assert retry.status_code == 200 and retry.json()["epoch"] == committed["state_epoch"]
        assert [e.turn_id for e in owner.service._characters[CAT].events] == ["after-confirmed-commit"]
        await owner.service.process_pending(CAT)
        newer = await owner.store.load(CAT)
        assert "after-confirmed-commit" in newer["subjects"][0]["evidence_turn_ids"]
        again = await owner.client.post(f"/api/proactive/recommendation/{mode}", json=body, headers=HEADERS)
        assert again.json() == retry.json()
        assert await owner.store.load(CAT) == newer
        altered = await owner.client.post(f"/api/proactive/recommendation/{mode}", headers=HEADERS,
            json={**body, "expected_confirmation": "f" * 64})
        assert altered.status_code == 409
        assert await owner.store.load(CAT) == newer
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
async def owner(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    settings = replace(TopicRecommendationSettings(), enabled=True)
    store = RecommendationStore(lambda: runtime_root, settings=settings)
    service = TopicRecommendationService(store, settings=settings)
    await service.start({CAT: "Yui"})
    await service.apply_controls(True, True, 1)
    monkeypatch.setattr(registry, "_service", service)
    from unittest.mock import Mock
    config = SimpleNamespace(load_characters=Mock(return_value={"猫娘": {
        "Yui": {"_reserved": {"character_id": CAT}}
    }}))
    monkeypatch.setattr(shared_state, "get_config_manager", lambda: config)
    monkeypatch.setattr(security, "AUTOSTART_CSRF_TOKEN", HEADERS["X-CSRF-Token"])
    app = FastAPI()
    app.include_router(proactive_router.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield SimpleNamespace(service=service, store=store, client=client, root=runtime_root, config=config)
    # No writer/reconciliation may escape its owning test.
    if recommendation_controls._pending_saves:
        await asyncio.gather(*recommendation_controls._pending_saves, return_exceptions=True)
    if recommendation_controls._reconciliations:
        await asyncio.gather(*recommendation_controls._reconciliations, return_exceptions=True)
    await service.close()


@pytest.mark.asyncio
async def test_status_is_readonly_actual_and_does_not_expose_private_records(owner):
    response = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] and data["availability"] == "ready"
    assert data["character_id"] == CAT and isinstance(data["epoch"], str)
    assert data["reset_generation"] == owner.service.reset_generation
    assert data["controls_enabled"] and data["capability_enabled"]
    assert data["counts"] == {"subjects": 0, "interests": 0, "deliveries": 0}
    assert "subjects" not in data and "prompt" not in data
    assert not (owner.root / "state").exists()
    assert response.headers["cache-control"] == "no-store"
    owner.service.settings = replace(owner.service.settings, enabled=False)
    response = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})
    assert response.json()["availability"] == "capability_disabled"


@pytest.mark.asyncio
async def test_recover_endpoint_is_confirmed_idempotent_and_preserves_profile(owner):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn())
    await owner.service.process_pending(CAT)
    before = await owner.store.load(CAT)
    proof = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": proof["epoch"], "request_id": "recover-api",
            "expected_confirmation": proof["reset_confirmation"], "expected_reset_generation": proof["reset_generation"]}
    rejected = await owner.client.post("/api/proactive/recommendation/recover", json=body)
    assert rejected.status_code == 403 and await owner.store.load(CAT) == before
    result = await owner.client.post("/api/proactive/recommendation/recover", json=body, headers=HEADERS)
    assert result.status_code == 200
    recovered = await owner.store.load(CAT)
    assert recovered["interests"] == before["interests"] and len(recovered["subjects"]) == 1
    assert recovered["subjects"][0]["context_confirmed"] is False
    assert recovered["state_epoch"] != before["state_epoch"] and owner.service._enabled()
    retry = await owner.client.post("/api/proactive/recommendation/recover", json=body, headers=HEADERS)
    assert retry.status_code == 200 and retry.json() == result.json()
    conflict = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
    assert conflict.status_code == 409 and await owner.store.load(CAT) == recovered


@pytest.mark.asyncio
@pytest.mark.parametrize("include_root_proof", [False, True])
async def test_reset_from_previous_service_instance_cannot_erase_new_evidence(owner, monkeypatch, include_root_proof):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn(turn_id="before-restart"))
    await owner.service.process_pending(CAT)
    status = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    previous_generation = owner.service.reset_generation
    assert status["reset_generation"] == previous_generation
    body = {"character_id": CAT, "expected_epoch": status["epoch"], "request_id": "pending-before-restart"}
    if include_root_proof:
        body["expected_reset_generation"] = previous_generation
        body["expected_confirmation"] = status["reset_confirmation"]
    await owner.service.close()
    replacement = TopicRecommendationService(
        RecommendationStore(lambda: owner.root, settings=owner.service.settings),
        analyzer=Analyzer(), settings=owner.service.settings)
    try:
        await replacement.start({CAT: "Yui"})
        await replacement.apply_controls(True, True, 1)
        monkeypatch.setattr(registry, "_service", replacement)
        new_sink = replacement.bind(CAT, "s2")
        new_sink.note_turn(turn("I am building a robot now", turn_id="after-restart", session_id="s2"))
        await replacement.process_pending(CAT)
        current = await replacement.store.load(CAT)
        assert current["state_epoch"] == status["epoch"]
        assert replacement.reset_generation != previous_generation
        assert current["subjects"][0]["evidence_turn_ids"] == ["before-restart", "after-restart"]
        result = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
        assert await replacement.store.load(CAT) == current
        assert result.status_code == (409 if include_root_proof else 400)
    finally:
        await replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("character_id,status,code", [
    ("../outside", 400, "invalid_character_id"),
    (OTHER, 404, "character_not_found"),
])
async def test_status_rejects_invalid_or_deleted_character(owner, character_id, status, code):
    result = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": character_id})
    assert result.status_code == status
    assert result.json() == {"success": False, "error_code": code}


@pytest.mark.asyncio
async def test_old_confirmation_cannot_cross_completed_maintenance(owner):
    from tests.unit.test_topic_recommendation_runtime import Analyzer, turn
    owner.service.analyzer = Analyzer()
    sink = owner.service.bind(CAT, "s1")
    sink.note_turn(turn(turn_id="before-maintenance"))
    await owner.service.process_pending(CAT)
    status = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": status["epoch"], "request_id": "before-maintenance",
            "expected_confirmation": status["reset_confirmation"], "expected_reset_generation": status["reset_generation"]}
    owner.service.set_maintenance(True)
    await owner.service.recover_after_maintenance()
    owner.service.set_maintenance(False)
    sink.note_turn(turn("I am building a robot now", turn_id="after-maintenance"))
    await owner.service.process_pending(CAT)
    current = await owner.store.load(CAT)
    assert current["state_epoch"] == status["epoch"]
    result = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
    assert await owner.store.load(CAT) == current
    assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["maintenance", "controls"])
@pytest.mark.parametrize("read_recovery", ["normal", "io_failure", "late_controls"])
async def test_committed_reset_interrupted_before_receipt_can_be_confirmed_without_new_chat(owner, monkeypatch, interruption, read_recovery):
    from main_logic.topic.recommendation.maintenance import recommendation_maintenance
    status = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": status["epoch"], "request_id": "committed-interrupted",
            "expected_confirmation": status["reset_confirmation"], "expected_reset_generation": status["reset_generation"]}
    loaded, release = asyncio.Event(), asyncio.Event()
    original_load = owner.store.load
    first = True

    async def delayed_load(character_id):
        nonlocal first
        state = await original_load(character_id)
        if first and state["state_epoch"] != status["epoch"]:
            first = False
            assert state["state_epoch"] != status["epoch"]  # Actual physical reset has committed.
            loaded.set()
            await release.wait()
        return state

    monkeypatch.setattr(owner.store, "load", delayed_load)
    request = asyncio.create_task(owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS))
    maintenance = None
    try:
        await asyncio.wait_for(loaded.wait(), 2)
        if interruption == "maintenance":
            async def maintain():
                async with recommendation_maintenance():
                    pass
            owner.service._characters[CAT].changed.clear()
            maintenance = asyncio.create_task(maintain())
            await asyncio.wait_for(owner.service._characters[CAT].changed.wait(), 2)
        else:
            await owner.service.apply_controls(False, False, 2)
    finally:
        release.set()
        result = await request
        if maintenance is not None:
            await maintenance
    assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"
    durable = await original_load(CAT)
    if read_recovery != "normal":
        from main_logic.topic.recommendation.contracts import RecommendationError

        async def interrupted_status_load(character_id):
            if read_recovery == "io_failure":
                raise RecommendationError("store_unavailable")
            state = await original_load(character_id)
            # Invalidate after the actual read, before the cache can adopt it.
            await owner.service.apply_controls(False, True, 3)
            return state

        monkeypatch.setattr(owner.store, "load", interrupted_status_load)
        failed = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})
        assert failed.status_code == (503 if read_recovery == "io_failure" else 409)
        assert failed.json()["success"] is False
        assert failed.json()["error_code"] == ("store_unavailable" if read_recovery == "io_failure" else "stale_operation")
        assert owner.service._characters[CAT].state["state_epoch"] == status["epoch"]
        assert await original_load(CAT) == durable
        monkeypatch.setattr(owner.store, "load", original_load)
    fresh = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    assert fresh["epoch"] == durable["state_epoch"]
    assert fresh["revision"] == durable["revision"]
    assert await original_load(CAT) == durable  # Status reconciliation has no disk mutation.
    retry = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": fresh["epoch"], "request_id": "fresh-confirmation",
              "expected_confirmation": fresh["reset_confirmation"], "expected_reset_generation": fresh["reset_generation"]})
    assert retry.status_code == 200


@pytest.mark.asyncio
async def test_reset_waiting_for_lock_cannot_cross_maintenance(owner):
    status = (await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})).json()
    body = {"character_id": CAT, "expected_epoch": status["epoch"], "request_id": "waiting-for-lock",
            "expected_confirmation": status["reset_confirmation"], "expected_reset_generation": status["reset_generation"]}
    slot = owner.service._characters[CAT]
    current = await owner.store.load(CAT)
    await slot.lock.acquire()
    slot.changed.clear()
    task = asyncio.create_task(owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS))
    try:
        await asyncio.wait_for(slot.changed.wait(), 2)
        owner.service.set_maintenance(True)
        owner.service.set_maintenance(False)
    finally:
        slot.lock.release()
        result = await task
    assert await owner.store.load(CAT) == current
    assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"


@pytest.mark.asyncio
async def test_status_cannot_attach_a_new_confirmation_to_a_pre_maintenance_result(owner, monkeypatch):
    async def ready():
        owner.service.set_maintenance(True)
        owner.service.set_maintenance(False)
        return True
    monkeypatch.setattr(owner.store, "root_ready", ready)
    result = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})
    assert result.status_code == 409 and result.json()["error_code"] == "stale_operation"


@pytest.mark.asyncio
async def test_unloaded_state_counts_are_unknown_not_zero(owner):
    owner.service._characters[CAT].state = None
    owner.service._characters[CAT].last_error = "state_corrupt"
    result = await owner.client.get("/api/proactive/recommendation/status", params={"character_id": CAT})
    assert result.status_code == 200
    data = result.json()
    assert data["availability"] == "degraded"
    assert data["epoch"] is None and data["revision"] is None
    assert data["counts"] == {"subjects": None, "interests": None, "deliveries": None}


@pytest.mark.asyncio
async def test_status_does_not_report_success_from_retired_owner(owner, monkeypatch):
    async def ready():
        monkeypatch.setattr(registry, '_service', None)
        return True
    monkeypatch.setattr(owner.store, 'root_ready', ready)
    response = await owner.client.get('/api/proactive/recommendation/status', params={'character_id': CAT})
    assert response.status_code == 409
    assert response.json()['error_code'] == 'stale_operation'


@pytest.mark.asyncio
async def test_strict_character_read_failure_cannot_become_default_identity(owner):
    owner.config.load_characters.side_effect = OSError('unavailable')
    response = await owner.client.get('/api/proactive/recommendation/status', params={'character_id': CAT})
    assert response.status_code == 503 and not response.json()['success']
    owner.config.load_characters.assert_called_once_with(require_authoritative=True)
    assert owner.service._characters[CAT].state is not None


@pytest.mark.asyncio
async def test_reset_requires_existing_csrf_and_origin_before_mutation(owner):
    epoch = (await owner.service.status(CAT))["epoch"]
    body = {"character_id": CAT, "expected_epoch": epoch, "request_id": "reset-one",
            "expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation}
    for headers in [{}, {**HEADERS, "Origin": "https://untrusted.example"}, {**HEADERS, "X-CSRF-Token": "wrong"}]:
        result = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=headers)
        assert result.status_code == 403
        assert result.json()["error_code"] == "csrf_validation_failed"
    assert not (owner.root / "state").exists()
    assert (await owner.service.status(CAT))["epoch"] == epoch


@pytest.mark.asyncio
@pytest.mark.parametrize("character_id,status,code", [
    ("../../outside", 400, "invalid_character_id"),
    (OTHER, 404, "character_not_found"),
])
async def test_reset_validates_real_identity_and_never_writes_unknown_role(owner, character_id, status, code):
    result = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS,
        json={"character_id": character_id, "expected_epoch": "opaque-epoch", "request_id": "invalid-role",
              "expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation})
    assert result.status_code == status and result.json()["error_code"] == code
    assert not (owner.root / "state").exists()


@pytest.mark.asyncio
async def test_reset_size_limit_and_maintenance_block_writes(owner):
    oversized = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS, content='"' + 'x' * 4096 + '"')
    assert oversized.status_code == 400 and oversized.json()["error_code"] == "invalid_request"
    epoch = (await owner.service.status(CAT))["epoch"]
    owner.service.set_maintenance(True)
    result = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": epoch, "request_id": "maintenance-reset",
              "expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation})
    assert result.status_code in (409, 503) and result.json()["success"] is False
    assert (await owner.service.status(CAT))["availability"] == "maintenance"
    assert (await owner.service.status(CAT))["epoch"] == epoch
    assert not list(owner.root.rglob("state.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], {}, {"character_id": CAT},
    {"character_id": CAT, "expected_epoch": "a", "request_id": "b", "score": 500},
    {"character_id": CAT, "expected_epoch": 1, "request_id": "b"},
    {"character_id": CAT, "expected_epoch": "a", "request_id": "x" * 129},
])
async def test_reset_validates_request_shape(owner, body):
    if isinstance(body, dict):
        body = {"expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation, **body}
    result = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
    assert result.status_code == 400
    assert result.json() == {"success": False, "error_code": "invalid_request"}


@pytest.mark.asyncio
async def test_reset_retries_idempotently_without_erasing_later_records(owner):
    epoch = (await owner.service.status(CAT))["epoch"]
    body = {"character_id": CAT, "expected_epoch": epoch, "request_id": "accepted-reset",
            "expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation}
    first = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
    assert first.status_code == 200
    receipt = first.json()
    assert receipt["success"] and receipt["epoch"] != epoch
    assert receipt["request_id"] == body["request_id"]
    assert receipt["reset_generation"] == body["expected_reset_generation"]
    state = await owner.store.load(CAT)
    state["interests"] = [{
        "subject_id": "later-interest", "summary": "created after reset",
        "basis": "inferred", "independent_conversations": 1,
        "updated_at": time.time(), "expires_at": time.time() + 86400,
    }]
    state = await owner.store.commit(CAT, state, expected_epoch=state["state_epoch"], expected_revision=state["revision"])
    owner.service._characters[CAT].state = state
    retry = await owner.client.post("/api/proactive/recommendation/reset", json=body, headers=HEADERS)
    assert retry.status_code == 200 and retry.json() == receipt
    assert (await owner.store.load(CAT))["interests"] == state["interests"]
    old_epoch = await owner.client.post("/api/proactive/recommendation/reset", json={**body, "request_id": "new-reset"}, headers=HEADERS)
    assert old_epoch.status_code == 409 and old_epoch.json()["error_code"] == "epoch_conflict"
    altered = await owner.client.post("/api/proactive/recommendation/reset", json={**body, "expected_epoch": receipt["epoch"]}, headers=HEADERS)
    assert altered.status_code == 409


@pytest.mark.asyncio
async def test_reset_disk_failure_does_not_report_success_or_change_epoch(owner, monkeypatch):
    epoch = (await owner.service.status(CAT))["epoch"]
    original_replace = store_module.os.replace

    def fail_state_replace(source, destination):
        if str(destination).endswith("state.json"):
            raise OSError("simulated disk failure")
        return original_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", fail_state_replace)
    result = await owner.client.post("/api/proactive/recommendation/reset", headers=HEADERS,
        json={"character_id": CAT, "expected_epoch": epoch, "request_id": "disk-failure",
              "expected_confirmation": owner.service.reset_confirmation(CAT), "expected_reset_generation": owner.service.reset_generation})
    assert result.status_code == 503 and not result.json()["success"]
    assert result.json()["error_code"] == "store_unavailable"
    assert (await owner.service.status(CAT))["epoch"] == epoch
    assert not list(owner.root.rglob("*.tmp"))
    assert not list(owner.root.rglob("state.json"))


def snapshot(revision, enabled=True):
    return ConversationSettingsSnapshot({"proactiveChatEnabled": enabled,
        "proactiveTopicRecommendationEnabled": enabled}, revision, None)


@pytest.mark.asyncio
async def test_unchanged_setting_sync_does_not_retire_active_recommendation_generation(owner, monkeypatch):
    async def latest(**kwargs):
        return snapshot(2)

    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot", latest)
    generation = owner.service._enable_generation
    result = await recommendation_controls.recommendation_aware_save(
        lambda settings: "saved", {"proactiveTopicRecommendationEnabled": True})
    assert result == "saved" and owner.service._enabled()
    assert owner.service._enable_generation == generation
    assert not recommendation_controls._pending_saves


@pytest.mark.asyncio
async def test_cancelled_settings_waiter_keeps_paused_until_physical_writer_and_latest_read(owner, monkeypatch):
    entered = asyncio.Event()
    reconciled = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def save(settings):
        assert settings == {"proactiveTopicRecommendationEnabled": False}
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(5):
            raise TimeoutError("test writer barrier not released")
        return "accepted"

    async def latest(**kwargs):
        assert kwargs == {"strict": True}
        return snapshot(3, enabled=False)

    original_apply = owner.service.apply_controls

    async def apply(*args, **kwargs):
        await original_apply(*args, **kwargs)
        reconciled.set()

    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot", latest)
    monkeypatch.setattr(owner.service, "apply_controls", apply)
    request = asyncio.create_task(recommendation_controls.recommendation_aware_save(
        save, {"proactiveTopicRecommendationEnabled": False}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not owner.service._enabled() and recommendation_controls._pending_saves
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert recommendation_controls._pending_saves and not owner.service._enabled()
    finally:
        release.set()
    await asyncio.wait_for(reconciled.wait(), 2)
    assert not recommendation_controls._pending_saves
    assert owner.service._config_revision == 3 and not owner.service._master


@pytest.mark.asyncio
async def test_late_settings_snapshot_cannot_restore_newer_opt_out(owner, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_snapshot(**kwargs):
        entered.set()
        await release.wait()
        return snapshot(1, enabled=True)

    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot", old_snapshot)
    refresh = asyncio.create_task(recommendation_controls.refresh_recommendation_controls())
    await entered.wait()
    await owner.service.apply_controls(False, False, 3)
    release.set()
    await refresh
    assert not owner.service._enabled() and owner.service._config_revision == 3


@pytest.mark.asyncio
async def test_settings_read_failure_or_new_writer_during_read_pauses_old_recommendations(owner, monkeypatch):
    async def unreadable(**kwargs):
        raise OSError("preferences read failed")

    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot", unreadable)
    await recommendation_controls.refresh_recommendation_controls()
    assert not owner.service._enabled()
    await owner.service.apply_controls(True, True, 2)
    entered, release = asyncio.Event(), asyncio.Event()

    async def reading(**kwargs):
        entered.set()
        await release.wait()
        return snapshot(2)

    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot", reading)
    refresh = asyncio.create_task(recommendation_controls.refresh_recommendation_controls())
    await entered.wait()
    owner.service.pause_controls()
    pending = asyncio.create_task(asyncio.Event().wait())
    recommendation_controls._pending_saves.add(pending)
    try:
        release.set()
        await refresh
        assert not owner.service._enabled()
    finally:
        recommendation_controls._pending_saves.discard(pending)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)

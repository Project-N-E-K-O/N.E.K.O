"""Real-store failure and retry regressions for PR 3375's architectural repair."""
import asyncio
import json
import stat
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from main_logic.topic.recommendation import maintenance, registry
from main_logic.topic.recommendation.analysis import RecommendationAnalyzer
from main_logic.topic.recommendation.contracts import RecommendationError
from main_logic.topic.recommendation.store import RecommendationStore, _real_directory, _safe_file
from main_routers import storage_location_router as routes
from tests.unit.test_topic_recommendation_runtime import CAT, setup, turn
from utils.storage.migration import _stat_is_reparse


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


async def publish(service, sink):
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    async with service._characters[CAT].lock:
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "d1",
                                           "How is the painting?", published_at=time.time() - 86401)


async def settle_publication(service):
    await service.flush_publications()
    worker = service._characters[CAT].task
    if worker:
        await asyncio.wait_for(asyncio.shield(worker), 2)


@pytest.mark.asyncio
async def test_unavailable_receipt_store_allows_root_recovery_and_optout_receipt_retry(tmp_path):
    service, sink, _, _ = await setup(tmp_path)
    available = [True]
    def root_guard():
        if not available[0]:
            raise RecommendationError("store_unavailable")
        return True
    service.store._root_guard = root_guard
    registry.configure_recommendation_service(service)
    try:
        await publish(service, sink)
        available[0] = False
        await service.apply_controls(True, False, 2)
        async with maintenance.recommendation_maintenance():
            assert service._maintenance
            assert service._characters[CAT].captures
            available[0] = True
        worker = service._characters[CAT].task
        if worker:
            await asyncio.wait_for(asyncio.shield(worker), 2)
        assert not service._maintenance
        assert not service._characters[CAT].captures
        assert (await service.store.load(CAT))["deliveries"][0]["delivery_id"] == "d1"
        assert not service._enabled()
    finally:
        available[0] = True
        registry.configure_recommendation_service(None)
        await service.close()


@pytest.mark.asyncio
async def test_flush_failure_still_seals_finished_store_and_releases_writer(tmp_path):
    service, sink, _, root = await setup(tmp_path)
    await publish(service, sink)
    service.store._root_guard = lambda: False
    try:
        with pytest.raises(RecommendationError, match="store_unavailable"):
            await service.close()
        assert service._characters[CAT].captures  # never falsely acknowledge flush
        assert service.store._closing.is_set()
        assert not service.store._operations
        assert service.store._lock_file is None
        contender = RecommendationStore(lambda: root)
        try:
            state = await contender.load(CAT)
            await contender.commit(CAT, state, expected_epoch=state["state_epoch"], expected_revision=state["revision"])
        finally:
            await contender.close()
    finally:
        await service.store.close()


@pytest.mark.asyncio
async def test_actual_storage_route_finishes_when_optional_finalizer_thread_stalls(tmp_path, monkeypatch):
    service, _, _, _ = await setup(tmp_path)
    service.settings = replace(service.settings, close_timeout=0.05)
    block = [False]
    entered, release = threading.Event(), threading.Event()
    def root_guard():
        if block[0]:
            entered.set()
            assert release.wait(5)
        return True
    service.store._root_guard = root_guard
    registry.configure_recommendation_service(service)
    async def transaction(*_args):
        block[0] = True
        return {"ok": True}
    monkeypatch.setattr(routes, "_post_storage_location_select_locked", transaction)
    task = asyncio.create_task(routes.post_storage_location_select(None, None))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert await asyncio.wait_for(asyncio.shield(task), 0.5) == {"ok": True}
        assert not maintenance._owners
        assert service._maintenance and service.store._operations
        # The route lock is free even though the optional fence read survives.
        async with asyncio.timeout(0.5):
            async with routes._storage_mutation_lock:
                pass
        block[0] = False
        release.set()
        await service.store.wait_idle(deadline=asyncio.get_running_loop().time() + 2)
        async with maintenance.recommendation_maintenance():
            pass
        assert not service._maintenance
    finally:
        block[0] = False
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        registry.configure_recommendation_service(None)
        await service.store.wait_idle(deadline=asyncio.get_running_loop().time() + 2)
        await service.close()


@pytest.mark.asyncio
async def test_live_physical_writer_blocks_recovery_with_bounded_error_and_keeps_lock(tmp_path, monkeypatch):
    service, sink, _, root = await setup(tmp_path)
    service.settings = replace(service.settings, close_timeout=0.04, store_timeout=0.04)
    entered, release = threading.Event(), threading.Event()
    original = service.store._replace
    def blocked_replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(service.store, "_replace", blocked_replace)
    registry.configure_recommendation_service(service)
    sink.note_turn(turn())
    analysis = asyncio.create_task(service.process_pending(CAT))
    contender = RecommendationStore(lambda: root)
    body_entered = False
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        async with asyncio.timeout(0.5):
            with pytest.raises(RecommendationError, match="closing_timeout"):
                async with maintenance.recommendation_maintenance():
                    body_entered = True
        assert not body_entered and not maintenance._owners
        assert service.store._operations and service.store._lock_file is not None
        current = await contender.load(CAT)
        with pytest.raises(RecommendationError, match="writer_unavailable"):
            await contender.commit(CAT, current, expected_epoch=current["state_epoch"], expected_revision=current["revision"])
        response = await routes._run_recommendation_storage_mutation(lambda: pytest.fail("Unsafe transaction entered"))
        assert response.status_code == 503
        assert json.loads(response.body)["reason_code"] == "recommendation_writer_busy"
    finally:
        release.set()
        await asyncio.gather(analysis, return_exceptions=True)
        await service.store.wait_idle(deadline=asyncio.get_running_loop().time() + 2)
        registry.configure_recommendation_service(None)
        await service.store.close()
        await contender.close()


def feedback_payload(payload):
    return json.dumps({"delivery_id": "d1", "related": True, "assessment": "disengaged", "reason": "explicit refusal",
        "evidence_refs": [t["ref"] for t in payload["turns"] if t["actor"] == "user"],
        "restriction": {"scope": "subject", "summary": "Do not discuss the painting", "angle": ""}})


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "timeout", "cancel"])
async def test_feedback_is_durable_before_candidate_failure_without_consuming_candidate_context(tmp_path, failure):
    service, sink, _, _ = await setup(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()
    valid = [False]
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        if payload["mode"] == "feedback":
            return feedback_payload(payload)
        if valid[0]:
            assert payload["existing_restrictions"][0]["restriction_id"] == "d1:subject"
            return '{"subjects":[]}'
        if failure == "cancel":
            started.set()
            await release.wait()
        if failure == "timeout":
            raise TimeoutError("controlled candidate timeout")
        return '{"subjects":"invalid"}'
    task = None
    try:
        await publish(service, sink)
        await settle_publication(service)
        service.analyzer = RecommendationAnalyzer(invoke=invoke)
        sink.note_turn(turn("Do not discuss the painting again", turn_id="refusal"))
        task = asyncio.create_task(service.process_pending(CAT))
        if failure == "cancel":
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises((RecommendationError, TimeoutError)):
                await task
        state = await service.store.load(CAT)
        assert state["deliveries"][0]["assessment"] == "disengaged"
        assert state["deliveries"][0]["feedback_revision"] == 1
        assert len(state["restrictions"]) == 1
        slot = service._characters[CAT]
        assert [e.turn_id for e in slot.events] == ["refusal"]
        assert slot.analyzed_watermark < slot.watermark
        assert service.snapshot(CAT) is None
        assert slot.analysis_commit is None
        valid[0] = True
        await service.process_pending(CAT)
        assert not slot.events and slot.analyzed_watermark == slot.watermark
        final = await service.store.load(CAT)
        assert final["deliveries"][0]["feedback_revision"] == 1
        assert final["revision"] == state["revision"] + 1
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.parametrize("tag,allowed", [(0x9000001A, True), (0xA0000003, False), (0xA000000C, False), (0, True)])
def test_recommendation_and_migration_share_reparse_policy(tag, allowed):
    info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=tag)
    path = SimpleNamespace(lstat=lambda: info)
    assert _stat_is_reparse(info) is not allowed
    assert _real_directory(path) is allowed
    info.st_mode = stat.S_IFREG
    if allowed:
        _safe_file(path)
    else:
        with pytest.raises(RecommendationError, match="store_unavailable"):
            _safe_file(path)


@pytest.mark.asyncio
async def test_feedback_retry_reconciles_cancelled_waiter_after_actual_atomic_replace(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = service.store._replace
    def after_replace(*args, **kwargs):
        state = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return state
    calls = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        calls.append(payload["mode"])
        return feedback_payload(payload) if payload["mode"] == "feedback" else '{"subjects":[]}'
    task = None
    try:
        await publish(service, sink)
        await settle_publication(service)
        service.analyzer = RecommendationAnalyzer(invoke=invoke)
        monkeypatch.setattr(service.store, "_replace", after_replace)
        sink.note_turn(turn("Do not discuss the painting", turn_id="refusal"))
        task = asyncio.create_task(service.process_pending(CAT))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        state = await service.store.load(CAT)
        assert state["deliveries"][0]["feedback_revision"] == 1
        assert len(service._characters[CAT].events) == 1
        assert service._characters[CAT].analysis_commit is None
        assert calls == ["feedback"]
        monkeypatch.setattr(service.store, "_replace", original)
        await service.process_pending(CAT)
        final = await service.store.load(CAT)
        assert final["deliveries"][0]["feedback_revision"] == 1
        assert len(final["restrictions"]) == 1
        assert final["revision"] == state["revision"] + 1
        assert not service._characters[CAT].events
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_shutdown_deadline_retains_live_thread_and_allows_physical_close_retry(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    await publish(service, sink)
    await settle_publication(service)
    entered, release = threading.Event(), threading.Event()
    original = service.store._read
    def blocked_read(identifier):
        entered.set()
        assert release.wait(5)
        return original(identifier)
    monkeypatch.setattr(service.store, "_read", blocked_read)
    read = asyncio.create_task(service.store.load(CAT))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        deadline = asyncio.get_running_loop().time() + 0.03
        async with asyncio.timeout(0.5):
            with pytest.raises(RecommendationError, match="closing_timeout"):
                await service.close(deadline=deadline)
        assert service.store._closing.is_set()
        assert service.store._lock_file is not None and service.store._operations
        release.set()
        await asyncio.gather(read, return_exceptions=True)
        await service.close(deadline=asyncio.get_running_loop().time() + 2)
        assert service.store._lock_file is None and not service.store._operations
    finally:
        release.set()
        await asyncio.gather(read, return_exceptions=True)
        await service.store.close()


@pytest.mark.asyncio
async def test_feedback_evaluator_never_invokes_candidate_discovery(monkeypatch):
    from tests import evaluate_topic_recommendation as evaluation
    from main_logic.topic.recommendation import analysis
    calls = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        calls.append(payload["mode"])
        assert payload["mode"] == "feedback"
        return json.dumps({"delivery_id": "d1", "related": False, "assessment": "unknown",
                           "reason": "test assessment", "evidence_refs": []})
    analyzer = RecommendationAnalyzer(invoke=invoke)
    monkeypatch.setattr(analysis, "RecommendationAnalyzer", lambda: analyzer)
    report = await evaluation.evaluate(True)
    assert report["sample_count"] >= 30 and calls
    assert set(calls) == {"feedback"}
    assert all(row["error"] is None for row in report["results"])

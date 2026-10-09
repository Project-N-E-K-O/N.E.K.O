"""Publication-boundary cancellation regressions found during strict review."""
import asyncio
import json
import threading
from types import SimpleNamespace
from dataclasses import replace

import pytest
from unittest.mock import AsyncMock

from tests.unit.test_topic_recommendation_runtime import CAT, setup, turn
from main_logic.topic.recommendation import registry
from main_logic.topic.recommendation.maintenance import recommendation_maintenance
from main_routers import recommendation_controls
from utils.preferences import ConversationSettingsSnapshot
from main_logic.topic.recommendation.contracts import RecommendationError
from main_logic.topic.recommendation.adapters import RecommendationBudgetedClient
from main_logic.topic.recommendation.analysis import RecommendationAnalyzer
from main_logic.topic.recommendation.analysis import validate_restriction_revocations
from utils.llm_client import HumanMessage, SystemMessage
from config.topic_recommendation_settings import TopicRecommendationSettings


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


@pytest.mark.asyncio
async def test_permission_arriving_during_refusal_analysis_uses_evidence_time(tmp_path, monkeypatch):
    from tests.unit import test_topic_recommendation_runtime as fixtures
    from main_logic.topic.recommendation import service as service_module
    clock = [fixtures.time.time()]
    local_clock = SimpleNamespace(time=lambda: clock[0], monotonic=fixtures.time.monotonic)
    monkeypatch.setattr(fixtures, "time", local_clock)
    monkeypatch.setattr(service_module, "time", local_clock)
    started, release = asyncio.Event(), asyncio.Event()
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        users = [t for t in payload["turns"] if t["actor"] == "user"]
        refs = [t["ref"] for t in users]
        refusing = any("Do not" in t["text"] for t in users)
        if payload["mode"] == "feedback":
            if refusing:
                started.set()
                await release.wait()
            return json.dumps({"delivery_id": payload["delivery"]["delivery_id"], "related": refusing,
                "assessment": "disengaged" if refusing else "unknown", "reason": "refusal" if refusing else "not feedback",
                "evidence_refs": refs if refusing else [],
                "restriction": {"scope": "subject", "summary": "Do not discuss painting", "angle": ""} if refusing else None})
        old = payload["existing_subjects"]
        permitting = any("permit" in t["text"] for t in users)
        return json.dumps({"subjects": [{"subject_id": old[0]["subject_id"] if old else None,
            "summary": "painting", "angle": "palette", "basis": "explicit", "status": "active", "evidence_refs": refs}],
            "restriction_revocations": [{"restriction_id": r["restriction_id"], "evidence_refs": refs}
                for r in payload["existing_restrictions"]] if permitting else []})
    service, sink, _, _ = await setup(tmp_path, analyzer=RecommendationAnalyzer(invoke=invoke))
    task = None
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        snapshot = service.snapshot(CAT)
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "d1", "How is the painting?",
                                           published_at=turn().timestamp - 86401)
        refusal = turn("Do not discuss painting", turn_id="refusal")
        sink.note_turn(refusal)
        task = asyncio.create_task(service.process_pending(CAT))
        await asyncio.wait_for(started.wait(), 5)
        clock[0] += 1
        permission = turn("I explicitly permit discussing painting again", turn_id="permission")
        sink.note_turn(permission)
        clock[0] += 1
        release.set()
        await task
        state = await service.store.load(CAT)
        assert state["restrictions"][0]["evidence_at"] == refusal.timestamp < permission.timestamp
        assert permission.timestamp < state["restrictions"][0]["updated_at"]
        await service.process_pending(CAT)
        assert not (await service.store.load(CAT))["restrictions"]
        assert not service._characters[CAT].events
        assert service.snapshot(CAT) is not None
    finally:
        release.set()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("correction_mode", ["candidate", "feedback"])
async def test_old_permission_cannot_revoke_newer_refusal_evidence(tmp_path, correction_mode):
    from main_logic.topic.recommendation.contracts import AnalysisResult, TurnEvidence, empty_state
    service, _, _, _ = await setup(tmp_path)
    try:
        state = empty_state(CAT)
        state["restrictions"] = [{"restriction_id": "d1:subject", "subject_id": "subject1", "scope": "subject",
            "summary": "Do not discuss painting", "angle": "", "evidence_at": 20, "updated_at": 30}]
        state["deliveries"] = [{"delivery_id": "d1", "subject_id": "subject1"}]
        evidence = TurnEvidence("u1", "u1", "s1", "user", "I permit discussing it", "en", 10, 1, "binding")
        if correction_mode == "candidate":
            result = AnalysisResult((), (), ({"restriction_id": "d1:subject", "evidence_refs": ["u1"]},))
        else:
            result = AnalysisResult((), ({"delivery_id": "d1", "related": True, "assessment": "engaged", "reason": "permission",
                "evidence_refs": ["u1"], "restriction": None, "revoke_restriction_ids": ["d1:subject"]},))
        with pytest.raises(RecommendationError, match="invalid_restriction"):
            service._merge(state, result, (evidence,))
        assert len(state["restrictions"]) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["characters", "tokens", "capacity"])
async def test_recovery_keeps_profile_and_requires_new_context(tmp_path, failure):
    from copy import deepcopy
    from main_logic.topic.recommendation.service import TopicRecommendationService
    from main_logic.topic.recommendation.store import RecommendationStore
    requests = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        requests.append(payload)
        refs = [t["ref"] for t in payload["turns"] if t["actor"] == "user"]
        if payload["mode"] == "feedback":
            return json.dumps({"delivery_id": payload["delivery"]["delivery_id"], "related": True,
                "assessment": "disengaged", "reason": "narrow refusal", "evidence_refs": refs,
                "restriction": {"scope": "angle", "summary": "do not compare scores", "angle": "scores"}})
        old = payload["existing_subjects"]
        return json.dumps({"subjects": [{"subject_id": old[0]["subject_id"] if old else None,
            "summary": "painting", "angle": "palette", "basis": "explicit", "status": "active", "evidence_refs": refs}]})
    analyzer = RecommendationAnalyzer(invoke=invoke)
    settings = replace(TopicRecommendationSettings(), enabled=True, max_events=1 if failure == "capacity" else 24,
                       debounce_seconds=3600, max_batch_wait_seconds=3600)
    service, sink, _, root = await setup(tmp_path, analyzer=analyzer, settings=settings)
    replacement = None
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        snapshot = service.snapshot(CAT)
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "d1", "How is the painting?",
                                           published_at=turn().timestamp - 86401)
        sink.note_turn(turn("No score comparisons", turn_id="refusal"))
        await service.process_pending(CAT)
        before = deepcopy(await service.store.load(CAT))
        sink.note_turn(turn("x" * 4001 if failure == "characters" else "word " * 600 if failure == "tokens" else "First pending", turn_id="bad"))
        if failure == "capacity":
            sink.note_turn(turn("Overflow", turn_id="overflow"))
        # Character rejection has no queued event until the next normal input.
        if failure == "characters":
            sink.note_turn(turn("A normal message", turn_id="normal-before-recovery"))
        with pytest.raises(RecommendationError, match="evidence_gap"):
            await service.process_pending(CAT)
        assert service.snapshot(CAT) is None
        receipt = await service.reset(CAT, before["state_epoch"], "recover-profile", preserve_profile=True)
        recovered = await service.store.load(CAT)
        for key in ("interests", "restrictions", "deliveries"):
            assert recovered[key] == before[key]
        assert [{k: v for k, v in s.items() if k != "context_confirmed"} for s in recovered["subjects"]] == [
            {k: v for k, v in s.items() if k != "context_confirmed"} for s in before["subjects"]]
        assert recovered["subjects"][0]["context_confirmed"] is False
        assert not service._characters[CAT].events and not service._characters[CAT].evidence_gap
        assert service.snapshot(CAT) is None and service._enabled()
        await service.close()
        replacement = TopicRecommendationService(RecommendationStore(lambda: root, settings=settings), analyzer, settings)
        await replacement.start({CAT: "Yui"})
        await replacement.apply_controls(True, True, 1)
        assert replacement.snapshot(CAT) is None
        sink = replacement.bind(CAT, "s2")
        sink.note_turn(turn("Let us discuss the painting palette", turn_id="fresh", session_id="s2"))
        await replacement.process_pending(CAT)
        assert replacement.snapshot(CAT).candidates[0]["context_confirmed"] is True
        assert len(replacement.restrictions_snapshot(CAT)) == 1
        newer = await replacement.store.load(CAT)
        assert await replacement.reset(CAT, before["state_epoch"], "recover-profile", preserve_profile=True) == receipt
        assert await replacement.store.load(CAT) == newer
        with pytest.raises(RecommendationError, match="epoch_conflict"):
            await replacement.reset(CAT, before["state_epoch"], "recover-profile")
        assert await replacement.store.load(CAT) == newer
    finally:
        if replacement:
            await replacement.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_permission", [False, True])
async def test_profile_correction_after_restart_is_not_feedback_to_old_delivery(tmp_path, explicit_permission):
    from main_logic.topic.recommendation.service import TopicRecommendationService
    from main_logic.topic.recommendation.store import RecommendationStore
    requests = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        requests.append(payload)
        refs = [t["ref"] for t in payload["turns"] if t["actor"] == "user"]
        if payload["mode"] == "feedback":
            return json.dumps({"delivery_id": payload["delivery"]["delivery_id"], "related": True,
                "assessment": "disengaged", "reason": "refusal", "evidence_refs": refs,
                "restriction": {"scope": "subject", "summary": "do not discuss this painting", "angle": ""}})
        old = payload["existing_subjects"]
        return json.dumps({"subjects": [{"subject_id": old[0]["subject_id"] if old else None,
            "summary": "painting", "angle": "palette", "basis": "explicit", "status": "active", "evidence_refs": refs}],
            "restriction_revocations": [{"restriction_id": r["restriction_id"], "evidence_refs": refs}
                for r in payload["existing_restrictions"]] if explicit_permission and any("permit" in t["text"] for t in payload["turns"]) else []})
    analyzer = RecommendationAnalyzer(invoke=invoke)
    service, sink, _, root = await setup(tmp_path, analyzer=analyzer)
    replacement = None
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        snapshot = service.snapshot(CAT)
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "old-opening", "How is your painting?")
        sink.note_turn(turn("Do not discuss this painting", turn_id="refuse"))
        await service.process_pending(CAT)
        assert len(service.restrictions_snapshot(CAT)) == 1
        await service.close()
        replacement = TopicRecommendationService(RecommendationStore(lambda: root), analyzer, service.settings)
        await replacement.start({CAT: "Yui"})
        await replacement.apply_controls(True, True, 1)
        sink = replacement.bind(CAT, "new-session")
        requests.clear()
        sink.note_turn(turn("I explicitly permit discussing the painting again" if explicit_permission else "I like the palette",
                            turn_id="correction", session_id="new-session"))
        await replacement.process_pending(CAT)
        assert [r["mode"] for r in requests] == ["candidates"]
        assert requests[0]["existing_restrictions"][0]["restriction_id"]
        assert len((await replacement.store.load(CAT))["restrictions"]) == (0 if explicit_permission else 1)
        assert (await replacement.store.load(CAT))["deliveries"][0]["feedback_revision"] == 1
    finally:
        if replacement:
            await replacement.close()
        await service.close()


@pytest.mark.parametrize("values", [
    [{"restriction_id": "foreign", "evidence_refs": ["u1"]}],
    [{"restriction_id": "r1", "evidence_refs": ["ai1"]}],
    [{"restriction_id": "r1", "evidence_refs": []}],
    [{"restriction_id": "r1", "evidence_refs": ["u1"], "path": "outside"}],
])
def test_profile_correction_rejects_unowned_or_missing_evidence(values):
    with pytest.raises(RecommendationError):
        validate_restriction_revocations(values, allowed_refs={"u1"}, restriction_ids={"r1"})


def delay_after_replace(monkeypatch, store):
    replaced, release = threading.Event(), threading.Event()
    original = store._replace

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        replaced.set()
        assert release.wait(5), "physical writer was not released"
        return result

    monkeypatch.setattr(store, "_replace", delayed)
    return replaced, release, original


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_stage", ["publication", "recovery"])
async def test_uncertain_recovery_preserves_publication_and_later_input(tmp_path, monkeypatch, cancel_stage):
    service, sink, _, _ = await setup(tmp_path)
    task = None
    release = threading.Event()
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        monkeypatch.setattr(service, "_wake", lambda slot: None)
        snapshot = service.snapshot(CAT)
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"],
                                           "before-recovery", "How is your painting?")
        if cancel_stage == "recovery":
            await service.flush_publications()
        sink.note_turn(turn("Pending old context", turn_id="old-context"))
        replaced, release, original = delay_after_replace(monkeypatch, service.store)
        task = asyncio.create_task(service.reset(CAT, snapshot.state_epoch, "uncertain-recovery", preserve_profile=True))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        durable = await service.store.load(CAT)
        assert durable["deliveries"][0]["delivery_id"] == "before-recovery"
        monkeypatch.setattr(service.store, "_replace", original)
        sink.note_turn(turn("New complete context", turn_id="after-recovery"))
        receipt = await service.reset(CAT, snapshot.state_epoch, "uncertain-recovery", preserve_profile=True)
        recovered = await service.store.load(CAT)
        assert recovered["state_epoch"] == receipt["epoch"] != snapshot.state_epoch
        assert recovered["deliveries"] == durable["deliveries"]
        assert recovered["subjects"][0]["subject_id"] == snapshot.candidates[0]["subject_id"]
        assert recovered["subjects"][0]["context_confirmed"] is False
        assert [event.turn_id for event in service._characters[CAT].events] == ["after-recovery"]
        assert await service.reset(CAT, snapshot.state_epoch, "uncertain-recovery", preserve_profile=True) == receipt
        assert await service.store.load(CAT) == recovered
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invent_revocation", [False, True])
async def test_oversized_restriction_context_keeps_discovery_but_grants_no_revocation(tmp_path, invent_revocation):
    from main_logic.topic.recommendation.contracts import TurnEvidence, empty_state
    from main_logic.topic.recommendation.service import _validate_profile
    state = empty_state(CAT)
    state["restrictions"] = [{"restriction_id": f"r{i}", "subject_id": "subject1", "scope": "subject",
                              "summary": "word " * 150, "angle": "", "updated_at": 5} for i in range(1, 65)]
    _validate_profile(state)
    requests = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        requests.append(payload)
        return json.dumps({"subjects": [], "restriction_revocations": [
            {"restriction_id": "r1", "evidence_refs": ["u1"]}] if invent_revocation else []})
    analyzer = RecommendationAnalyzer(invoke=invoke)
    evidence = TurnEvidence("u1", "u1", "new-session", "user", "Let us discuss my new painting", "en", 10, 1, "binding")
    if invent_revocation:
        with pytest.raises(RecommendationError, match="invalid_restriction"):
            await analyzer.analyze((evidence,), state)
    else:
        result = await analyzer.analyze((evidence,), state)
        assert not result.restriction_revocations
    assert len(requests) == 1 and requests[0]["existing_restrictions"] == []
    assert len(state["restrictions"]) == 64


@pytest.mark.asyncio
async def test_correction_cannot_revoke_refusal_newer_than_its_user_evidence(tmp_path):
    from main_logic.topic.recommendation.contracts import AnalysisResult, TurnEvidence, empty_state
    service, _, _, _ = await setup(tmp_path)
    try:
        old = empty_state(CAT)
        old["restrictions"] = [{"restriction_id": "r1", "subject_id": "subject1", "scope": "subject",
                                "summary": "Do not discuss the painting", "angle": "", "updated_at": 20}]
        evidence = TurnEvidence("u1", "u1", "s1", "user", "I permit discussing it", "en", 10, 1, "binding")
        result = AnalysisResult((), (), ({"restriction_id": "r1", "evidence_refs": ["u1"]},))
        with pytest.raises(RecommendationError, match="invalid_restriction"):
            service._merge(old, result, (evidence,))
        assert len(old["restrictions"]) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_analysis_recovers_when_cancelled_after_physical_replace(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    replaced, release, original = delay_after_replace(monkeypatch, service.store)
    task = None
    try:
        sink.note_turn(turn())
        task = asyncio.create_task(service.process_pending(CAT))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        durable = await service.store.load(CAT)
        assert durable["revision"] == 1
        assert service._characters[CAT].state["revision"] == 0
        monkeypatch.setattr(service.store, "_replace", original)
        sink.note_turn(turn("I continued the painting", turn_id="u2"))
        await service.process_pending(CAT)
        state = await service.store.load(CAT)
        assert state["subjects"][0]["evidence_turn_ids"] == ["u1", "u2"]
        assert state["interests"][0]["independent_conversations"] == 1
        assert service.snapshot(CAT) is not None
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_result", [False, True])
async def test_uncertain_analysis_retires_committed_batch_without_model_reinterpretation(tmp_path, monkeypatch, empty_result):
    batches = []

    async def invoke(*, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        users = [item for item in payload["turns"] if item["actor"] == "user"]
        batches.append([item["ref"] for item in users])
        # New IDs and empty discoveries are both valid model responses.
        return json.dumps({"subjects": [] if empty_result else [
            {"subject_id": None, "summary": item["text"], "angle": "ask about progress",
             "basis": "inferred", "status": "active", "evidence_refs": [item["ref"]]}
            for item in users]})

    service, sink, _, _ = await setup(tmp_path, analyzer=RecommendationAnalyzer(invoke=invoke))
    replaced, release, original = delay_after_replace(monkeypatch, service.store)
    task = None
    try:
        sink.note_turn(turn())
        task = asyncio.create_task(service.process_pending(CAT))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert (await service.store.load(CAT))["revision"] == 1
        monkeypatch.setattr(service.store, "_replace", original)
        sink.note_turn(turn("I am building a robot now", turn_id="u2"))
        await service.process_pending(CAT)
        assert batches == [["turn:s1:user:u1"], ["turn:s1:user:u2"]]
        state = await service.store.load(CAT)
        assert [identifier for item in state["subjects"] for identifier in item["evidence_turn_ids"]] == (
            [] if empty_result else ["u1", "u2"])
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_worker_recovers_uncertain_analysis_without_new_user_input(tmp_path, monkeypatch):
    settings = replace(TopicRecommendationSettings(), enabled=True, debounce_seconds=0,
                       max_batch_wait_seconds=0, store_timeout=0.5)
    service, sink, analyzer, _ = await setup(tmp_path, settings=settings)
    monkeypatch.setattr(service, "_wake", lambda slot: None)
    replaced, release, _ = delay_after_replace(monkeypatch, service.store)
    failed = asyncio.Event()
    error_code = service._error_code

    def observe_failure(exc):
        result = error_code(exc)
        failed.set()
        return result

    monkeypatch.setattr(service, "_error_code", observe_failure)
    task = None
    try:
        sink.note_turn(turn())
        task = asyncio.create_task(service._worker(service._characters[CAT]))
        assert await asyncio.to_thread(replaced.wait, 5)
        await asyncio.wait_for(failed.wait(), 5)
        assert service._characters[CAT].last_error == "analysis_timeout"
        release.set()
        service._characters[CAT].changed.set()
        await asyncio.wait_for(task, 5)
        assert len(analyzer.calls) == 1
        assert not service._characters[CAT].events
        assert service.snapshot(CAT)
        assert (await service.status(CAT))["availability"] == "ready"
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_analysis_cancelled_before_replace_keeps_its_evidence_for_retry(tmp_path, monkeypatch):
    service, sink, analyzer, _ = await setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = service.store._replace

    def before_replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.store, "_replace", before_replace)
    task = None
    try:
        sink.note_turn(turn())
        task = asyncio.create_task(service.process_pending(CAT))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert (await service.store.load(CAT))["revision"] == 0
        monkeypatch.setattr(service.store, "_replace", original)
        await service.process_pending(CAT)
        state = await service.store.load(CAT)
        assert len(analyzer.calls) == 2
        assert state["revision"] == 1
        assert state["subjects"][0]["evidence_turn_ids"] == ["u1"]
        assert not service._characters[CAT].events
        assert service._characters[CAT].analysis_commit is None
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reset", "delete", "recover_delete"])
async def test_retirement_clears_uncertain_analysis_intent(tmp_path, monkeypatch, operation):
    service, sink, _, _ = await setup(tmp_path)
    monkeypatch.setattr(service, "_wake", lambda slot: None)
    replaced, release, original = delay_after_replace(monkeypatch, service.store)
    task = None
    try:
        slot = service._characters[CAT]
        sink.note_turn(turn())
        task = asyncio.create_task(service.process_pending(CAT))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert (await service.store.load(CAT))["revision"] == 1
        assert slot.analysis_commit is not None
        monkeypatch.setattr(service.store, "_replace", original)
        if operation == "reset":
            await service.reset(CAT, slot.state["state_epoch"], "reset-uncertain-analysis")
            assert not (await service.store.load(CAT))["subjects"]
        else:
            if operation == "recover_delete":
                delete = service.store.delete
                monkeypatch.setattr(service.store, "delete", AsyncMock(side_effect=RecommendationError("store_unavailable")))
                with pytest.raises(RecommendationError, match="store_unavailable"):
                    await service.delete_character(CAT)
                assert slot.analysis_commit is not None
                monkeypatch.setattr(service.store, "delete", delete)
                service.set_maintenance(True)
                await service.recover_after_maintenance()
            else:
                await service.delete_character(CAT)
            assert slot.deleted_cleanup_complete
        assert slot.analysis_commit is None
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


class WireClient:
    def __init__(self):
        self.calls = []
        self.closed = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True
    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        return SimpleNamespace(content="reply")
    async def astream(self, messages, **kwargs):
        self.calls.append(messages)
        yield SimpleNamespace(content="reply")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["ainvoke", "astream"])
async def test_actual_text_request_budget_blocks_sdk_and_does_not_truncate(operation):
    wire = WireClient()
    async with RecommendationBudgetedClient(wire, 64) as client:
        legal = [SystemMessage(content="base"), HumanMessage(content="hello")]
        if operation == "ainvoke":
            await client.ainvoke(legal)
        else:
            assert [chunk async for chunk in client.astream(legal)]
        assert wire.calls == [legal]
        oversized = [legal[0], HumanMessage(content="new restriction " * 500)]
        with pytest.raises(RecommendationError, match="input_budget_exceeded"):
            if operation == "ainvoke":
                await client.ainvoke(oversized)
            else:
                [chunk async for chunk in client.astream(oversized)]
        assert wire.calls == [legal]
        assert oversized[1].content == "new restriction " * 500
    assert wire.closed


@pytest.mark.asyncio
async def test_text_budget_preserves_existing_vision_payload_and_counts_its_text():
    wire = WireClient()
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + "a" * 10000}}
    async with RecommendationBudgetedClient(wire, 64) as client:
        legal = [SystemMessage(content="base"), HumanMessage(content=[image, {"type": "text", "text": "hello"}])]
        await client.ainvoke(legal)
        assert wire.calls == [legal]
        assert wire.calls[0][1].content[0] is image
        oversized = [legal[0], HumanMessage(content=[image, {"type": "text", "text": "restriction " * 500}])]
        with pytest.raises(RecommendationError, match="input_budget_exceeded"):
            await client.ainvoke(oversized)
        assert wire.calls == [legal]
    assert wire.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_phase2_injects_the_budget_into_its_actual_client_factory(monkeypatch, enabled):
    from main_logic.proactive_chat import generation
    from main_logic.proactive_chat.contracts import ProactiveChatResult
    wire = WireClient()
    async def factory(*args, **kwargs):
        return wire
    async def generate(**kwargs):
        async with await kwargs["make_llm"]() as client:
            await client.ainvoke(kwargs["messages"])
        return generation.Phase2Generation(result=ProactiveChatResult(body={"action": "pass"}))
    monkeypatch.setattr(generation, "_make_proactive_llm", factory)
    monkeypatch.setattr(generation, "_generate_phase2_stream", generate)
    arguments = dict(mgr=SimpleNamespace(), proactive_sid="p1", lanlan_name="Yui", proactive_lang="en",
        master_name="Master", model_config=SimpleNamespace(has_vision_model=False, conversation_model="test"),
        system_prompt="restriction " * 500, dynamic_context="", screenshot_b64=None, focus_thinking=False,
        expects_source_tag=True, active_channels=["chat"], selected_music_link=None, selected_meme_link=None,
        music_content=None, meme_content=None, is_playing_music=False, music_cooldown=False,
        recommendation_input_budget=64 if enabled else None)
    if enabled:
        with pytest.raises(RecommendationError, match="input_budget_exceeded"):
            await generation._run_phase2_generation(**arguments)
        assert not wire.calls
    else:
        assert (await generation._run_phase2_generation(**arguments)).result.body["action"] == "pass"
        assert len(wire.calls) == 1
    assert wire.closed


@pytest.mark.asyncio
async def test_reset_preserves_an_evidence_gap_after_its_accepted_cutoff(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.store.reset

    async def delayed(*args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(service.store, "reset", delayed)
    task = None
    try:
        sink.note_turn(turn())
        task = asyncio.create_task(service.reset(CAT, service._characters[CAT].state["state_epoch"], "gap-reset"))
        await asyncio.wait_for(entered.wait(), 5)
        sink.note_turn(turn("Do not pursue this topic " * 500, turn_id="u2"))
        release.set()
        await task
        assert service._characters[CAT].evidence_gap
        assert (await service.status(CAT))["availability"] == "degraded"
        assert service.snapshot(CAT) is None
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_failed_control_refresh_does_not_restore_previous_authorization(tmp_path):
    service, _, _, _ = await setup(tmp_path)
    service.controls_refresher = AsyncMock(side_effect=OSError("strict read failed"))
    try:
        service.set_maintenance(True)
        await service.recover_after_maintenance()
        service.set_maintenance(False)
        assert (await service.status(CAT))["availability"] == "degraded"
        assert not service._enabled()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_maintenance_reloads_saved_optout_before_resuming(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    monkeypatch.setattr(registry, "_service", service)
    monkeypatch.setattr(recommendation_controls, "aload_global_conversation_settings_snapshot",
                        AsyncMock(return_value=ConversationSettingsSnapshot(
                            settings={"proactiveChatEnabled": True,
                                      "proactiveTopicRecommendationEnabled": False}, revision=2,
                            asr_decision=None)))
    # Assembly injects the existing settings reconciliation, not a domain→router import.
    service.controls_refresher = recommendation_controls.refresh_recommendation_controls
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        assert service.snapshot(CAT)
        async with recommendation_maintenance():
            assert service.snapshot(CAT) is None
        assert (await service.status(CAT))["availability"] == "user_disabled"
        assert service.snapshot(CAT) is None
        count = len(service._characters[CAT].events)
        sink.note_turn(turn("must not be collected", turn_id="after-optout"))
        assert len(service._characters[CAT].events) == count
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_uncertain_reset_retry_preserves_input_after_original_cutoff(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    replaced, release, original = delay_after_replace(monkeypatch, service.store)
    task = None
    try:
        sink.note_turn(turn())
        epoch = service._characters[CAT].state["state_epoch"]
        task = asyncio.create_task(service.reset(CAT, epoch, "uncertain-reset"))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        durable = await service.store.load(CAT)
        assert durable["state_epoch"] != epoch
        monkeypatch.setattr(service.store, "_replace", original)
        sink.note_turn(turn("This is my new painting task", turn_id="u2"))
        receipt = await service.reset(CAT, epoch, "uncertain-reset")
        assert receipt["epoch"] == durable["state_epoch"]
        assert [e.turn_id for e in service._characters[CAT].events] == ["u2"]
        await service.process_pending(CAT)
        assert (await service.store.load(CAT))["subjects"][0]["evidence_turn_ids"] == ["u2"]
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_receipt_flush_cannot_replay_evidence_cleared_by_uncertain_reset(tmp_path, monkeypatch):
    service, sink, _, _ = await setup(tmp_path)
    task = None
    release = threading.Event()
    try:
        sink.note_turn(turn())
        await service.process_pending(CAT)
        monkeypatch.setattr(service, "_wake", lambda slot: None)
        snapshot = service.snapshot(CAT)
        assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"],
                                           "before-reset", "How is your painting?")
        sink.note_turn(turn("Old task evidence", turn_id="u2"))
        epoch = snapshot.state_epoch
        replaced, release, original = delay_after_replace(monkeypatch, service.store)
        task = asyncio.create_task(service.reset(CAT, epoch, "reset-with-receipt"))
        assert await asyncio.to_thread(replaced.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await service.store.load(CAT)
        monkeypatch.setattr(service.store, "_replace", original)
        sink.note_turn(turn("New task evidence", turn_id="u3"))
        await service.flush_publications()
        await service.reset(CAT, epoch, "reset-with-receipt")
        assert [e.turn_id for e in service._characters[CAT].events] == ["u3"]
        await service.process_pending(CAT)
        state = await service.store.load(CAT)
        assert state["subjects"][0]["evidence_turn_ids"] == ["u3"]
        assert not state["deliveries"]
    finally:
        release.set()
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()

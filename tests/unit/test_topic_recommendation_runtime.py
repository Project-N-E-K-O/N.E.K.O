import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from config.topic_recommendation_settings import TopicRecommendationSettings, get_topic_recommendation_settings
from config.prompts.prompts_topic_recommendation import ANALYSIS_INSTRUCTIONS, DELIVERY_INSTRUCTIONS, analysis_prompt
from main_logic.topic.recommendation.contracts import AnalysisResult, RecommendationError, empty_state, validate_state
from main_logic.topic.recommendation.service import TopicRecommendationService
from main_logic.topic.recommendation.store import RecommendationStore
from main_logic.topic.recommendation.analysis import RecommendationAnalyzer, validate_subjects, validate_feedback, redact_credentials
from main_logic.topic.recommendation.adapters import ReadOnlyMemoryAdapter, build_candidate_prompt, parse_recommendation_choice

CAT = "character_" + "a" * 32
OTHER = "character_" + "b" * 32


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    yield


def turn(text="I am working on my blue painting", actor="user", turn_id="u1", **overrides):
    values = dict(raw_text=text, actor=actor, turn_id=turn_id, input_mode="text", session_id="s1", synthetic=False,
                  timestamp=time.time(), lang="en")
    values.update(overrides)
    return SimpleNamespace(**values)


class Analyzer:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.calls = []
        self.feedback = ()

    async def analyze(self, events, state, memories=(), *, on_feedback=None):
        self.calls.append((events, state, memories))
        self.started.set()
        await self.release.wait()
        feedback = self.feedback
        if on_feedback is not None:
            state = await on_feedback(feedback)
            feedback = ()
        evidence = [e for e in events if e.actor == "user"]
        old = state["subjects"]
        return AnalysisResult(({"subject_id": old[0]["subject_id"] if old else None, "summary": "the blue painting",
                                "angle": "ask about the palette", "basis": "inferred", "status": "active",
                                "evidence_refs": [e.ref for e in evidence]},), feedback)

    async def output_allowed(self, text, restrictions, language):
        return "painting" not in text

    async def choice_matches(self, text, candidate, language):
        return "painting" in text


async def setup(tmp_path, *, settings=None, analyzer=None):
    root = tmp_path / "runtime"
    root.mkdir(exist_ok=True)
    settings = settings or replace(TopicRecommendationSettings(), enabled=True, debounce_seconds=3600, max_batch_wait_seconds=3600)
    store = RecommendationStore(lambda: root, settings=settings)
    analyzer = analyzer or Analyzer()
    service = TopicRecommendationService(store, analyzer, settings)
    await service.start({CAT: "Yui"})
    await service.apply_controls(True, True, 1)
    sink = service.bind(CAT, "s1")
    return service, sink, analyzer, root


def state_path(root):
    return root / "state" / "recommendation" / CAT / "state.json"


@pytest.mark.asyncio
async def test_default_off_and_no_automatic_data_or_analysis(tmp_path, monkeypatch):
    monkeypatch.delenv("NEKO_TOPIC_RECOMMENDATION_ENABLED", raising=False)
    assert not get_topic_recommendation_settings().enabled
    service, sink, analyzer, root = await setup(tmp_path, settings=TopicRecommendationSettings())
    sink.note_turn(turn())
    assert not analyzer.calls and not state_path(root).exists()
    assert (await service.status(CAT))["availability"] == "capability_disabled"
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"input_mode": "voice"}, {"input_mode": None}, {"turn_id": None}, {"synthetic": True}, {"session_id": "old"}])
async def test_true_text_identity_required(tmp_path, override):
    service, sink, analyzer, root = await setup(tmp_path)
    sink.note_turn(turn(**override))
    assert not service._characters[CAT].events
    assert not analyzer.calls and not state_path(root).exists()
    await service.close()


@pytest.mark.asyncio
async def test_dedup_incremental_evidence_and_snapshot_persistence(tmp_path):
    service, sink, analyzer, root = await setup(tmp_path)
    event = turn()
    sink.note_turn(event)
    sink.note_turn(event)
    assert len(service._characters[CAT].events) == 1
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    assert snapshot and service.is_current(snapshot)
    assert snapshot.candidates[0]["basis"] == "inferred"
    assert service._characters[CAT].state["interests"][0]["independent_conversations"] == 1
    sink.note_turn(event)
    assert not service._characters[CAT].events
    assert state_path(root).exists()
    saved = json.loads(state_path(root).read_text(encoding="utf-8"))
    await service.close()
    service2, sink2, _, _ = await setup(tmp_path)
    assert service2._characters[CAT].state == saved
    assert service2.snapshot(CAT).candidates[0]["subject_id"] == snapshot.candidates[0]["subject_id"]
    await service2.close()


@pytest.mark.asyncio
async def test_new_user_watermark_blocks_old_snapshot_until_latest_batch_understood(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    initial = service.snapshot(CAT)
    analyzer.started.clear()
    analyzer.release.clear()
    sink.note_turn(turn("I tried a different palette", turn_id="u2"))
    batch = asyncio.create_task(service.process_pending(CAT))
    await analyzer.started.wait()
    sink.note_turn(turn("Do not ask about my painting now", turn_id="u3"))
    assert not service.is_current(initial) and service.snapshot(CAT) is None
    assert (await service.status(CAT))["availability"] == "waiting_context"
    analyzer.release.set()
    await batch
    assert service.snapshot(CAT) is None
    assert service._characters[CAT].state["revision"] == 2
    await service.process_pending(CAT)
    assert service.snapshot(CAT)
    await service.close()


@pytest.mark.asyncio
async def test_disable_during_model_wait_cannot_commit_or_publish(tmp_path):
    service, sink, analyzer, root = await setup(tmp_path)
    analyzer.release.clear()
    sink.note_turn(turn())
    batch = asyncio.create_task(service.process_pending(CAT))
    await analyzer.started.wait()
    await service.apply_controls(False, True, 2)
    analyzer.release.set()
    with pytest.raises(RecommendationError, match="stale_operation"):
        await batch
    assert not state_path(root).exists()
    sink.note_turn(turn("closed-period input", turn_id="ignored"))
    assert len(service._characters[CAT].events) == 1
    await service.apply_controls(True, True, 3)
    await service.process_pending(CAT)
    assert service.snapshot(CAT)
    await service.close()


@pytest.mark.asyncio
async def test_control_cache_rejects_old_revision_and_strict_failure(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    await service.apply_controls(False, False, 3)
    await service.apply_controls(True, True, 2)
    assert not service._enabled()
    await service.apply_controls(True, True, 4, valid=False)
    assert (await service.status(CAT))["availability"] == "degraded"
    service.pause_controls()
    sink.note_turn(turn())
    assert not service._characters[CAT].events
    await service.close()


@pytest.mark.asyncio
async def test_publication_capture_precedes_fast_reply_and_preserves_refusal(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    subject = snapshot.candidates[0]["subject_id"]
    assert service.capture_publication(snapshot, subject, "delivery1", "How is your painting?")
    assert service.capture_publication(snapshot, subject, "delivery1", "How is your painting?")
    event = turn("I'd rather not talk about that painting", turn_id="u2")
    sink.note_turn(event)
    ref = service._characters[CAT].events[-1].ref
    analyzer.feedback = ({"delivery_id": "delivery1", "assessment": "disengaged", "related": True,
                          "reason": "specific refusal", "evidence_refs": [ref],
                          "restriction": {"scope": "subject", "summary": "do not discuss the painting", "angle": ""}},)
    await service.process_pending(CAT)
    state = service._characters[CAT].state
    assert len(state["deliveries"]) == 1
    assert state["deliveries"][0]["publication_status"] == "server_committed"
    assert "viewer_seen" not in state["deliveries"][0]
    assert state["deliveries"][0]["assessment"] == "disengaged"
    assert service.snapshot(CAT) is None
    assert not await service.output_allowed(CAT, "Let's discuss your painting")
    assert await service.output_allowed(CAT, "How was your breakfast?")
    sink.note_turn(turn("Actually the painting went quite well", turn_id="u3"))
    ref2 = service._characters[CAT].events[-1].ref
    analyzer.feedback = ({**analyzer.feedback[0], "assessment": "engaged", "evidence_refs": [ref2], "restriction": None},)
    await service.process_pending(CAT)
    assert state is not service._characters[CAT].state
    assert len(service._characters[CAT].state["restrictions"]) == 1
    assert service._characters[CAT].state["deliveries"][0]["feedback_revision"] == 2
    await service.close()


@pytest.mark.asyncio
async def test_reset_idempotent_retry_preserves_later_evidence_and_controls(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    epoch = (await service.status(CAT))["epoch"]
    receipt = await service.reset(CAT, epoch, "request1")
    sink.note_turn(turn())
    await service.process_pending(CAT)
    newer = service._characters[CAT].state["revision"]
    sink.note_turn(turn("new evidence", turn_id="u2"))
    assert await service.reset(CAT, epoch, "request1") == receipt
    assert len(service._characters[CAT].events) == 1
    assert service._characters[CAT].state["revision"] == newer
    assert service._enabled()
    with pytest.raises(RecommendationError, match="epoch_conflict"):
        await service.reset(CAT, epoch, "request2")
    await service.close()


@pytest.mark.asyncio
async def test_capacity_gap_is_not_negative_or_restored_positive(tmp_path):
    settings = replace(TopicRecommendationSettings(), enabled=True, max_events=1, debounce_seconds=3600, max_batch_wait_seconds=3600)
    service, sink, analyzer, _ = await setup(tmp_path, settings=settings)
    sink.note_turn(turn())
    sink.note_turn(turn("Do not talk about this", turn_id="u2"))
    assert service._characters[CAT].evidence_gap
    with pytest.raises(RecommendationError, match="evidence_gap"):
        await service.process_pending(CAT)
    assert service.snapshot(CAT) is None and not analyzer.calls
    assert (await service.status(CAT))["last_error"] == "evidence_gap"
    epoch = (await service.status(CAT))["epoch"]
    await service.reset(CAT, epoch, "recover")
    assert not service._characters[CAT].evidence_gap
    await service.close()


@pytest.mark.asyncio
async def test_binding_replacement_rename_and_delete_are_isolated(tmp_path):
    service, sink, analyzer, root = await setup(tmp_path)
    await service.sync_characters({CAT: "Renamed", OTHER: "Other"})
    assert service._characters[CAT].name == "Renamed"
    sink.note_turn(turn())
    assert not service._characters[CAT].events
    sink = service.bind(CAT, "s1", display_name="Renamed")
    sink.note_turn(turn())
    await service.process_pending(CAT)
    service.unbind(CAT, "wrong")
    assert service.snapshot(CAT)
    service.unbind(CAT, "s1")
    other_sink = service.bind(CAT, "s2")
    sink.note_turn(turn(turn_id="old"))
    assert not service._characters[CAT].events
    other_sink.note_turn(turn(session_id="s2", turn_id="new"))
    await service.process_pending(CAT)
    assert service.snapshot(CAT, "s1") is None and service.snapshot(CAT, "s2")
    await service.delete_character(CAT)
    other_sink.note_turn(turn(session_id="s2", turn_id="deleted"))
    assert not state_path(root).exists()
    with pytest.raises(RecommendationError, match="invalid_character_id"):
        await service.status(CAT)
    assert (await service.status(OTHER))["counts"]["subjects"] == 0
    await service.close()


@pytest.mark.asyncio
async def test_expiry_missing_candidates_and_guarded_choice(tmp_path):
    service, sink, _, _ = await setup(tmp_path)
    assert service.snapshot(CAT) is None
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    prompt = build_candidate_prompt(snapshot, "ja")
    assert "R1" in prompt and snapshot.candidates[0]["summary"] in prompt
    clean, selected, valid = parse_recommendation_choice("[CHAT][REC:R1] How is it going?", snapshot)
    assert clean == "[CHAT] How is it going?" and selected == snapshot.candidates[0]["subject_id"] and valid
    assert parse_recommendation_choice("[REC:NONE] Other topic", snapshot)[2]
    for text in ("No choice", "[REC:R3] invalid", "[REC:R1][REC:NONE] ambiguous", "[REC:../path] invalid"):
        assert not parse_recommendation_choice(text, snapshot)[2]
    assert not parse_recommendation_choice("[REC:R1] invalid", None)[2]
    service._characters[CAT].state["subjects"][0]["expires_at"] = time.time() - 1
    assert service.snapshot(CAT) is None
    service.set_maintenance(True)
    assert (await service.status(CAT))["availability"] == "maintenance"
    await service.close()


def test_state_and_model_schema_validate_without_accepting_foreign_evidence():
    state = empty_state(CAT)
    validate_state(state)
    for bad in ({}, {**state, "revision": True}, {**state, "state_epoch": "reused"}, {**state, "schema_version": 2}, {**state, "subjects": "bad"}):
        with pytest.raises(RecommendationError):
            validate_state(bad)
    with pytest.raises(RecommendationError, match="capacity_exhausted"):
        validate_state({**state, "subjects": [{}] * 65})
    valid = {"subjects": [{"subject_id": None, "summary": "painting", "angle": "palette", "basis": "inferred", "status": "active", "evidence_refs": ["u1"]}]}
    assert validate_subjects(valid, allowed_refs={"u1"}, existing_ids=set())
    for field, value in (("evidence_refs", ["ai1"]), ("basis", "certain"), ("subject_id", "foreign"), ("summary", ""), ("path", "../file")):
        bad = {"subjects": [{**valid["subjects"][0], field: value}]}
        with pytest.raises(RecommendationError):
            validate_subjects(bad, allowed_refs={"u1"}, existing_ids=set())


@pytest.mark.parametrize("assessment", ["engaged", "disengaged", "unknown"])
def test_feedback_semantic_states_do_not_depend_on_approval_words(assessment):
    payload = {"delivery_id": "d1", "related": True, "assessment": assessment,
               "reason": "contextual understanding", "evidence_refs": ["u1"], "restriction": None}
    assert validate_feedback(payload, allowed_refs={"u1"}, delivery_id="d1")["assessment"] == assessment
    assert validate_feedback({**payload, "related": False}, allowed_refs={"u1"}, delivery_id="d1")["assessment"] == "unknown"
    with pytest.raises(RecommendationError):
        validate_feedback({**payload, "evidence_refs": ["not supplied"]}, allowed_refs={"u1"}, delivery_id="d1")


@pytest.mark.asyncio
async def test_real_analysis_total_messages_budget_redaction_and_invalid_outputs(tmp_path):
    captured = []

    async def invoke(**kwargs):
        captured.append(kwargs)
        value = json.loads(kwargs["messages"][1]["content"])
        user = next(t for t in value["turns"] if t["actor"] == "user")
        return json.dumps({"subjects": [{"subject_id": None, "summary": "painting", "angle": "palette", "basis": "inferred", "status": "active", "evidence_refs": [user["ref"]]}]})

    analyzer = RecommendationAnalyzer(invoke=invoke)
    service, sink, _, _ = await setup(tmp_path, analyzer=analyzer)
    sink.note_turn(turn("I paint with blue. api_key=sk-123456789abcdef"))
    await service.process_pending(CAT)
    assert "sk-123456789abcdef" not in str(captured)
    assert captured[0]["max_completion_tokens"] == 1200 and captured[0]["timeout"] == 15
    assert "temperature" not in captured[0]
    assert service.snapshot(CAT)
    await service.close()
    tiny = RecommendationAnalyzer(replace(TopicRecommendationSettings(), candidate_input_tokens=1), invoke=invoke)
    with pytest.raises(RecommendationError, match="input_budget_exceeded"):
        await tiny._invoke(system="large", payload={"data": "x"}, budget=1, output=1200, timeout=15)
    for response in ("not json", "[]", '{"subjects":"not a list"}'):
        async def bad(**kwargs):
            return response
        bad_analyzer = RecommendationAnalyzer(invoke=bad)
        if response != '{"subjects":"not a list"}':
            with pytest.raises(RecommendationError, match="invalid_model_output"):
                await bad_analyzer._invoke(system="s", payload={}, budget=5000, output=1200, timeout=15)


@pytest.mark.asyncio
async def test_memory_adapter_is_read_only_scoped_and_error_empty_is_failure():
    requests = []
    result = {"results": [{"id": "f1", "text": "an old painting memory"}]}

    class Client:
        async def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return SimpleNamespace(status_code=200, json=lambda: result)

    adapter = ReadOnlyMemoryAdapter(lambda: Client())
    scope = ({"subject_kind": "participant", "subject_id": "user_scope"},)
    items = await adapter.read(display_name="Yui Test", subjects=scope, query="painting", language="en")
    assert items[0]["ref"] == "f1" and not items[0]["reliable_time"]
    assert requests[0][0].endswith("/query_memory/Yui%20Test")
    assert requests[0][1]["json"]["subjects"] == list(scope)
    await adapter.read(display_name="Yui Test", subjects=None, allow_private=True, query="painting", language="en")
    assert "subjects" not in requests[1][1]["json"]
    with pytest.raises(RecommendationError, match="invalid_memory_scope"):
        await adapter.read(display_name="Yui", subjects=None, query="x", language="en")
    with pytest.raises(RecommendationError, match="invalid_memory_scope"):
        await adapter.read(display_name="Yui", subjects=(), query="x", language="en")
    result = {"results": [], "error_code": "hybrid_recall_failed"}
    with pytest.raises(RecommendationError, match="memory_read_failed"):
        await adapter.read(display_name="Yui", subjects=scope, query="x", language="en")


def test_eight_locales_and_credential_redaction():
    locales = {"en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"}
    assert set(ANALYSIS_INSTRUCTIONS) == set(DELIVERY_INSTRUCTIONS) == locales
    for language in locales:
        assert "======以上为" in analysis_prompt(language)
    assert "zh" not in ANALYSIS_INSTRUCTIONS
    assert "secret" not in redact_credentials("password=secret")


@pytest.mark.asyncio
async def test_configured_summary_client_factory_and_context_close(monkeypatch):
    from utils import config_manager, llm_client
    calls = []

    class Config:
        async def aget_model_api_config(self, tier):
            calls.append(tier)
            return {"model": "configured-model", "base_url": "http://configured-model.invalid", "api_key": "configured-key", "provider_type": "configured-provider"}

    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            calls.append("closed")
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content='{"subjects":[]}')

    async def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return Client()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: Config())
    monkeypatch.setattr(llm_client, "create_chat_llm_async", factory)
    result = await RecommendationAnalyzer()._invoke(system="safe", payload={}, budget=5000, output=1200, timeout=15)
    assert result == {"subjects": []}
    assert calls[0] == "summary" and calls[-1] == "closed"
    args, options = calls[1]
    assert args == ("configured-model", "http://configured-model.invalid", "configured-key")
    assert options == {"max_completion_tokens": 1200, "timeout": 15, "max_retries": 0, "provider_type": "configured-provider"}
    Config.aget_model_api_config = lambda *_: asyncio.sleep(0, result={})
    with pytest.raises(RecommendationError, match="model_config_unavailable"):
        await RecommendationAnalyzer()._invoke(system="safe", payload={}, budget=5000, output=1200, timeout=15)


@pytest.mark.asyncio
async def test_real_semantic_feedback_explicit_revocation_and_output_guard(tmp_path):
    requests = []
    revoke = False

    async def invoke(**kwargs):
        value = json.loads(kwargs["messages"][1]["content"])
        requests.append(value)
        if value["mode"] == "output_guard":
            return '{"allowed":false}'
        refs = [t["ref"] for t in value["turns"] if t["actor"] == "user"]
        if value["mode"] == "feedback":
            return json.dumps({"delivery_id": value["delivery"]["delivery_id"], "related": True,
                "assessment": "engaged" if revoke else "disengaged", "reason": "explicit permission" if revoke else "narrow user refusal",
                "evidence_refs": refs, "restriction": None if revoke else {"scope": "angle", "summary": "do not compare competition scores", "angle": "competition scores"},
                "revoke_restriction_ids": [r["restriction_id"] for r in value["existing_restrictions"]] if revoke else []})
        existing = value["existing_subjects"]
        return json.dumps({"subjects": [{"subject_id": existing[0]["subject_id"] if existing else None, "summary": "painting",
                            "angle": "palette", "basis": "explicit", "status": "active", "evidence_refs": refs}]})

    analyzer = RecommendationAnalyzer(invoke=invoke)
    service, sink, _, _ = await setup(tmp_path, analyzer=analyzer)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "d1", "How was the score?")
    sink.note_turn(turn("No score comparison please", turn_id="u2"))
    await service.process_pending(CAT)
    assert len(service.restrictions_snapshot(CAT)) == 1
    assert not await service.output_allowed(CAT, "Let's discuss those scores")
    revoke = True
    sink.note_turn(turn("Now I am comfortable discussing scores", turn_id="u3"))
    await service.process_pending(CAT)
    assert not service.restrictions_snapshot(CAT)
    assert any(r["mode"] == "feedback" for r in requests) and any(r["mode"] == "output_guard" for r in requests)
    await service.close()


@pytest.mark.parametrize("mutate", [
    {"restriction": {"scope": "subject", "summary": "no", "angle": ""}, "assessment": "engaged"},
    {"restriction": {"scope": "topic", "summary": "no", "angle": ""}},
    {"restriction": {"scope": "angle", "summary": "no", "angle": ""}},
    {"restriction": {"scope": "angle", "summary": "no", "angle": "scores", "path": "file"}},
    {"related": "true"}, {"assessment": "certain"}, {"delivery_id": "foreign"},
    {"evidence_refs": "u1"}, {"reason": ""}, {"revoke_restriction_ids": ["foreign"]},
])
def test_feedback_schema_rejects_unproven_or_foreign_restrictions(mutate):
    payload = {"delivery_id": "d1", "related": True, "assessment": "disengaged", "reason": "direct refusal",
               "evidence_refs": ["u1"], "restriction": None}
    with pytest.raises(RecommendationError):
        validate_feedback({**payload, **mutate}, allowed_refs={"u1"}, delivery_id="d1", restriction_ids={"r1"})


@pytest.mark.asyncio
async def test_invalid_output_guard_fails_closed_without_changing_profile(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    slot = service._characters[CAT]
    slot.state["restrictions"] = [{"restriction_id": "r1", "subject_id": slot.state["subjects"][0]["subject_id"],
                                  "scope": "subject", "summary": "painting is restricted", "angle": ""}]
    state_before = json.dumps(slot.state, sort_keys=True)
    async def bad(*args):
        raise RecommendationError("invalid_model_output")
    analyzer.output_allowed = bad
    assert not await service.output_allowed(CAT, "a paraphrase of that painting")
    assert json.dumps(slot.state, sort_keys=True) == state_before
    prompt = build_candidate_prompt(None, "zh-CN", restrictions=service.restrictions_snapshot(CAT))
    assert "[REC:" not in prompt
    assert not parse_recommendation_choice("my actual speech [REC:R1]", service.snapshot(CAT))[2]
    await service.close()


@pytest.mark.asyncio
async def test_profile_read_failure_reports_unknown_counts_and_no_overwrite(tmp_path):
    service, _, _, root = await setup(tmp_path)
    await service.close()
    path = state_path(root)
    path.parent.mkdir(parents=True)
    state = empty_state(CAT)
    state["subjects"] = [{"subject_id": "invalid", "summary": "missing business fields"}]
    path.write_text(json.dumps(state), encoding="utf-8")
    original = path.read_bytes()
    restarted, sink, analyzer, _ = await setup(tmp_path)
    assert (await restarted.status(CAT))["counts"]["subjects"] is None
    assert (await restarted.status(CAT))["last_error"] == "state_corrupt"
    sink.note_turn(turn())
    assert not analyzer.calls and path.read_bytes() == original
    await restarted.close()


@pytest.mark.asyncio
async def test_controls_unrelated_revision_and_maintenance_resume(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    initial_generation = service._enable_generation
    assert service.controls_match_payload({"volume": 0.5})
    assert service.controls_match_payload({"proactiveTopicRecommendationEnabled": True})
    assert not service.controls_match_payload({}, full_snapshot=True)
    assert not service.controls_match_payload({"proactiveTopicRecommendationEnabled": False})
    await service.apply_controls(True, True, 2)
    assert service._enable_generation == initial_generation
    service.set_maintenance(True)
    assert service.control_token(CAT) is None
    sink.note_turn(turn())
    assert not service._characters[CAT].events
    service.set_maintenance(False)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    assert service.control_token_current(service.control_token(CAT))
    await service.close()


@pytest.mark.asyncio
async def test_pending_session_evidence_is_safely_taken_over_and_replay_deduplicated(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    event = turn()
    sink.note_turn(event)
    service.unbind(CAT, "s1")
    assert service.snapshot(CAT) is None
    assert len(service._characters[CAT].events) == 1
    new_sink = service.bind(CAT, "s2")
    new_sink.note_turn(turn(session_id="s2", turn_id=event.turn_id))
    assert len(service._characters[CAT].events) == 1
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT, "s2")
    assert snapshot and not service._characters[CAT].evidence_gap
    assert analyzer.calls[0][0][0].session_id == "s1"
    sink.note_turn(turn(turn_id="late-old-binding"))
    assert not service._characters[CAT].events
    await service.close()
    restarted, new_sink, _, _ = await setup(tmp_path)
    new_sink.note_turn(turn())
    assert not restarted._characters[CAT].events
    await restarted.close()


@pytest.mark.asyncio
async def test_rec_marker_alone_cannot_claim_unrelated_dialogue_as_topic_delivery(tmp_path):
    service, sink, _, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    subject = snapshot.candidates[0]["subject_id"]
    assert not await service.validate_selection(snapshot, subject, "What weather do you like?")
    assert await service.validate_selection(snapshot, subject, "How is the painting coming along?")
    assert not await service.validate_selection(snapshot, "foreign", "painting")
    sink.note_turn(turn("new potentially correcting user evidence", turn_id="u2"))
    assert not await service.validate_selection(snapshot, subject, "painting")
    assert not service._characters[CAT].state["deliveries"]
    await service.close()


@pytest.mark.asyncio
async def test_real_selection_guard_rejects_metadata_and_ambiguous_output():
    requests = []
    async def invoke(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        requests.append(payload)
        return '{"adopted":false}'
    analyzer = RecommendationAnalyzer(invoke=invoke)
    assert not await analyzer.choice_matches("[REC:R1] Hello", {"summary": "painting", "angle": "palette", "basis": "inferred"}, "en")
    assert requests[0]["mode"] == "selection_guard"
    assert requests[0]["candidate"]["summary"] == "painting"
    async def bad(**kwargs):
        return '{"adopted":"yes"}'
    with pytest.raises(RecommendationError, match="invalid_model_output"):
        await RecommendationAnalyzer(invoke=bad).choice_matches("painting", {}, "en")


@pytest.mark.asyncio
async def test_private_memory_adapter_runs_only_after_real_binding_authorization(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    requests = []
    class Reader:
        async def read(self, **kwargs):
            requests.append(kwargs)
            return ({"ref": "f1", "text": "an old memory", "reliable_time": False},)
    service.memory_reader = Reader()
    service.bind(CAT, "s1", allow_private_memory=True)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    assert requests[0]["allow_private"] and requests[0]["subjects"] is None
    assert analyzer.calls[0][2][0]["ref"] == "f1"
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["config", "factory"])
async def test_model_deadline_includes_config_and_factory(monkeypatch, stage):
    from utils import config_manager, llm_client
    entered = asyncio.Event()
    blocker = asyncio.Event()
    async def config(_tier):
        if stage == "config":
            entered.set()
            await blocker.wait()
        return {"model": "m", "base_url": "http://test.invalid", "api_key": "key"}
    async def factory(*args, **kwargs):
        entered.set()
        await blocker.wait()
    async def count(_text):
        return 1
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(aget_model_api_config=config))
    monkeypatch.setattr(llm_client, "create_chat_llm_async", factory)
    monkeypatch.setattr("main_logic.topic.recommendation.analysis.acount_tokens", count)
    with pytest.raises(TimeoutError):
        await RecommendationAnalyzer()._invoke(system="safe", payload={}, budget=5000, output=700, timeout=.03)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_batch_deadline_preserves_evidence_and_releases_global_permit(tmp_path):
    settings = replace(TopicRecommendationSettings(), enabled=True, debounce_seconds=3600,
                       max_batch_wait_seconds=3600, global_concurrency=1, batch_timeout=.03)
    service, sink, analyzer, _ = await setup(tmp_path, settings=settings)
    analyzer.release.clear()
    sink.note_turn(turn())
    with pytest.raises(TimeoutError):
        await service.process_pending(CAT)
    assert service._characters[CAT].events and service._semaphore._value == 1
    assert not service._characters[CAT].state["subjects"]
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("close", [False, True])
async def test_accepted_publication_survives_optout_and_immediate_shutdown(tmp_path, close):
    service, sink, analyzer, root = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    assert service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "receipt", "How is the painting?")
    assert not service.is_current(snapshot) and service.snapshot(CAT) is None
    before_calls = len(analyzer.calls)
    await service.apply_controls(True, False, 2)
    if close:
        await service.close()
    else:
        await service.process_pending(CAT)
    persisted = json.loads(state_path(root).read_text(encoding="utf-8"))
    assert persisted["deliveries"][0]["delivery_id"] == "receipt"
    assert persisted["deliveries"][0]["publication_status"] == "server_committed"
    assert len(analyzer.calls) == before_calls
    if not close:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["selection", "restriction"])
@pytest.mark.parametrize("optout", [False, True])
async def test_all_model_guards_share_bounded_global_concurrency(tmp_path, guard, optout):
    settings = replace(TopicRecommendationSettings(), enabled=True, debounce_seconds=3600,
                       max_batch_wait_seconds=3600, global_concurrency=1, worker_wait_seconds=.03)
    service, sink, analyzer, _ = await setup(tmp_path, settings=settings)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    subject = snapshot.candidates[0]["subject_id"]
    service._characters[CAT].state["restrictions"] = [{"restriction_id": "r1", "subject_id": subject,
        "summary": "painting", "scope": "angle", "angle": "palette"}]
    model_calls = []
    async def forbidden(*args):
        model_calls.append(args)
        return True
    analyzer.choice_matches = forbidden
    analyzer.output_allowed = forbidden
    await service._semaphore.acquire()
    call = (service.validate_selection(snapshot, subject, "painting") if guard == "selection"
            else service.output_allowed(CAT, "painting"))
    task = asyncio.create_task(call)
    if optout:
        await asyncio.sleep(0)  # Let the guard capture its token before opt-out.
        await service.apply_controls(True, False, 2)
        service._semaphore.release()
    assert await task is False
    assert not model_calls
    if not optout:
        assert service._semaphore._value == 0
        service._semaphore.release()
    assert service._semaphore._value == 1
    await service.close()


@pytest.mark.asyncio
async def test_unconfirmed_name_binding_does_not_mutate_authorized_identity(tmp_path):
    service, sink, _, _ = await setup(tmp_path)
    original = service._characters[CAT].binding_generation
    with pytest.raises(RecommendationError, match="character_identity_unconfirmed"):
        service.bind(CAT, "wrong-session", "Dirty name")
    assert service.binding_is_current(CAT, "s1", original, "Yui")
    sink.note_turn(turn())
    assert service._characters[CAT].events
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"published_at": float("nan")}, {"published_at": True},
                                      {"published_at": -1}, {"speech_id": []}, {"text": " "}])
async def test_invalid_publication_metadata_cannot_corrupt_persisted_profile(tmp_path, overrides):
    service, sink, _, _ = await setup(tmp_path)
    sink.note_turn(turn())
    await service.process_pending(CAT)
    snapshot = service.snapshot(CAT)
    arguments = {"text": "How is your painting?", **overrides}
    assert not service.capture_publication(snapshot, snapshot.candidates[0]["subject_id"], "invalid", **arguments)
    assert not service._characters[CAT].captures
    await service.close()


@pytest.mark.asyncio
async def test_rename_retires_inflight_memory_query_without_losing_real_evidence(tmp_path):
    service, sink, analyzer, _ = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    names = []
    class Reader:
        async def read(self, **kwargs):
            names.append(kwargs["display_name"])
            entered.set()
            await release.wait()
            return ()
    service.memory_reader = Reader()
    sink = service.bind(CAT, "s1", "Yui", allow_private_memory=True)
    sink.note_turn(turn())
    operation = asyncio.create_task(service.process_pending(CAT))
    await entered.wait()
    await service.sync_characters({CAT: "Renamed"})
    assert not service.binding_is_current(CAT, "s1", sink.binding_generation)
    release.set()
    with pytest.raises(RecommendationError, match="stale_operation"):
        await operation
    assert not analyzer.calls and service._characters[CAT].events
    service.bind(CAT, "s2", "Renamed", allow_private_memory=True)
    await service.process_pending(CAT)
    assert names == ["Yui", "Renamed"] and service.snapshot(CAT)
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_real_input_arriving_during_reset_is_not_lost(tmp_path, monkeypatch, fail):
    service, sink, analyzer, _ = await setup(tmp_path)
    sink.note_turn(turn())
    entered, release = asyncio.Event(), asyncio.Event()
    reset = service.store.reset
    async def paused(*args, **kwargs):
        entered.set()
        await release.wait()
        if fail:
            raise OSError("unavailable")
        return await reset(*args, **kwargs)
    monkeypatch.setattr(service.store, "reset", paused)
    task = asyncio.create_task(service.reset(CAT, service._characters[CAT].state["state_epoch"], "reset"))
    await entered.wait()
    sink.note_turn(turn("a new actual painting task", turn_id="u2"))
    release.set()
    if fail:
        with pytest.raises(OSError):
            await task
    else:
        await task
    events = service._characters[CAT].events
    assert "u2" in [e.turn_id for e in events]
    assert (await service.status(CAT))["availability"] == ("degraded" if fail else "waiting_context")
    if not fail:
        await service.process_pending(CAT)
        assert [e.turn_id for e in analyzer.calls[-1][0]] == ["u2"]
    await service.close()

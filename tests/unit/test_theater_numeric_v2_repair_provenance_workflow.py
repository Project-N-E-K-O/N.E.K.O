"""A same-turn wire draft aids editing without becoming evidence or persisted state."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_performance import performance_fingerprint
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, KEY
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.parametrize("mode", ["valid", "stale", "different_join", "wrong_type", "missing"])
def test_editable_projection_uses_only_exact_matching_entry_fields(mode):
    environment, trigger = "展柜旁灯光明亮。", "（小岚揭开遮板）"
    body = {"segments": [
        {"phase": "source_response", "performance": "我愿意展示。"},
        {"phase": "transition_bridge", "scene_narration": ""},
        {"phase": "target_opening", "scene_narration": environment + "\n" + trigger,
         "performance": "内容已经展示。"},
    ], "suggested_inputs": []}
    original = deepcopy(body)
    provenance = {"body_fingerprint": performance_fingerprint(body), "entry_fields": {
        "target_scene_narration": environment, "target_entry_trigger": trigger}}
    if mode == "stale": provenance["body_fingerprint"] = "old-draft"
    elif mode == "different_join": provenance["entry_fields"]["target_entry_trigger"] = "（小岚收起遮板）"
    elif mode == "wrong_type": provenance["entry_fields"]["target_entry_trigger"] = None
    elif mode == "missing": provenance = None
    draft = wf._transition_repair_draft(body, draft_provenance=provenance)
    assert body == original
    assert draft["target_performance"] == "内容已经展示。"
    if mode == "valid":
        assert draft["target_scene_narration"] == environment and draft["target_entry_trigger"] == trigger
    else:
        assert "target_entry_trigger" not in draft
        assert draft["target_scene_narration"] == environment + "\n" + trigger


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "stale"])
async def test_tail_rewrite_receives_wire_fields_and_keeps_storage_canonical(tmp_path, monkeypatch, mode):
    story = deepcopy(_engine().story)
    target = next(row for row in story["nodes"] if row["id"] == "work")
    target["story_beat"]["fixed_narrations"] = [{"id": "entry_record", "text": "记录本中显示本周的接收日期。",
        "trigger": {"type": "entry"}, "after": [], "required_before_exit": True}]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=mode, catgirl_binding=_binding(),
        opening_performance={"performance": "先商量是否展示记录。", "suggested_inputs": []})
    path = runtime.store._path(mode); original = path.read_bytes()
    message, source = "请你展示记录，你同意吗？", "我同意展示。"
    environment, first_trigger = "桌旁灯光明亮。", "（手指停在封皮上方）"
    calls = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
            fact_audit=({"key": KEY, "subject": "双方", "action": "同意", "object": "展示",
                "result": "已商定", "evidence": [{"source": "player_input", "quote": message}]},))

    async def generate(self, **kwargs):
        assert path.read_bytes() == original
        if kwargs["outcome"].route is None:
            calls.append("source")
            return {"performance": source, "suggested_inputs": []}
        calls.append("tail")
        trigger = first_trigger if calls.count("tail") == 1 else "（小岚翻开记录本）"
        if calls.count("tail") == 2:
            draft = kwargs["draft_repair_context"]["draft"]
            assert kwargs["transition_source"]["performance"] == source
            assert draft["source_performance"] == source
            if mode == "valid":
                assert draft["target_scene_narration"] == environment
                assert draft["target_entry_trigger"] == first_trigger
            else:
                assert "target_entry_trigger" not in draft
                assert draft["target_scene_narration"] == environment + "\n" + first_trigger
        candidate = engine.finalize_transition_performance(kwargs["outcome"], {
            "source_performance": source, "bridge_scene_narration": "",
            "target_scene_narration": environment + "\n" + trigger,
            "target_performance": "记录就在这里。", "suggested_inputs": [],
        }, target_opening=environment, bridge_required=False)
        kwargs["draft_provenance_sink"].update({
            "body_fingerprint": performance_fingerprint(candidate) if mode == "valid" else "stale",
            "entry_fields": {"target_scene_narration": environment, "target_entry_trigger": trigger}})
        return candidate

    async def contract(self, **kwargs):
        assert path.read_bytes() == original
        if not kwargs["actor_performance"].get("segments"):
            kwargs["bound_completion_sink"].append((0,))
            return ()
        calls.append("target_review")
        if calls.count("target_review") == 1:
            kwargs["diagnostics"]["findings"] = [{"rule": "原文之前须有实际翻开动作。", "quote": first_trigger,
                "candidate": {"id": "c1", "phase": "target_opening", "type": "narration",
                              "text": environment + "\n" + first_trigger}, "prior": None}]
            return ("原文之前须有实际翻开动作。",)
        return ()

    async def forbidden(*args, **kwargs): raise AssertionError("source fact already independently reviewed")
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", forbidden)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "沉稳。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("show", 0, message), ensure_current_binding=lambda _: _binding())
    assert calls == ["source", "tail", "target_review", "tail", "target_review"]
    assert result.stored.session.revision == 1 and result.stored.session.current_node_id == "work"
    assert result.performance["segments"][0]["performance"] == source
    assert "entry_fields" not in path.read_text(encoding="utf-8") and "target_entry_trigger" not in path.read_text(encoding="utf-8")
    assert "target_entry_trigger" not in json.dumps(result.performance, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["valid", "invalid"])
async def test_bad_review_quote_never_causes_actor_to_rewrite_legal_prose(tmp_path, monkeypatch, repair):
    runtime = NumericV2Runtime(_engine(), tmp_path)
    current = await runtime.start_session(session_id=repair, catgirl_binding=_binding(),
        opening_performance={"performance": "先商量安排。", "suggested_inputs": []})
    path = runtime.store._path(repair); original = path.read_bytes()
    player, role = "请你展示物品。", "（轻轻点头）我们先把安排说清楚。"
    generations, requests = [], []

    async def evaluate(self, **kwargs): return ev.NumericV2EvaluationResult((), False)
    async def generate(self, **kwargs):
        generations.append(kwargs)
        assert len(generations) == 1, "a malformed audit is not a prose violation"
        return {"performance": role, "suggested_inputs": []}
    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            assert path.read_bytes() == original
            requests.append(messages)
            if len(requests) == 1:
                payload = contract_audit_payload({"violations": []}, messages)
                properties = kwargs["response_format"]["json_schema"]["schema"]["properties"]
                if "premature_departure" in properties: payload["premature_departure"] = []
                if "route_conflicts" in properties: payload["route_conflicts"] = []
                if "fact_discoveries" in properties:
                    payload["fact_discoveries"] = []
                payload["player_authority"]["c0"] = [{"quote": player, "authorization_ref": "p0"}]
                return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
            assert set(kwargs["response_format"]["json_schema"]["schema"]["properties"]) == {"player_authority"}
            row = "none" if repair == "valid" else [{"quote": player, "authorization_ref": "p0"}]
            return SimpleNamespace(content=json.dumps({"player_authority": {"c0": row}}, ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "沉稳。")
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("show", 0, player), ensure_current_binding=lambda _: _binding())
    if repair == "invalid":
        with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_contract_check_failed"):
            await wf._execute_numeric_v2_turn(**kwargs)
        assert path.read_bytes() == original
    else:
        result = await wf._execute_numeric_v2_turn(**kwargs)
        assert result.performance["performance"] == role
        assert result.stored.session.revision == 1
    assert len(generations) == 1 and len(requests) == 2

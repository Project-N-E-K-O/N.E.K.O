"""Metadata schedules a bounded joint claim; independent evidence still decides it."""

import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_shared_history_scheduling import agreement_engine
from tests.unit.test_theater_numeric_v2_source_delivery_routing import KEY
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.fixture(autouse=True)
def _separate_evaluator_path(monkeypatch):
    # Keep coverage of standalone Evaluator proposals; combined source drafts have their own regression module.
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)


async def _exercise(tmp_path, monkeypatch, *, mode="agree", topic="equipment", metadata="valid"):
    noun = "设备" if topic == "equipment" else "档案"
    message = f"我想把{noun}留在原处，你同意吗？"
    description = f"双方已同意将{noun}留在原处。"
    role = f"我同意，就把{noun}留在原处。"
    if mode == "refuse": role = "我不同意这个安排。"
    elif mode == "unclear": role = "我还需要想一想，没有决定。"
    elif mode == "onlyrequest": role = "你希望我同意这个安排吗？"
    elif mode == "actualexperience":
        description = "玩家已完整体验展示并给出自己的核对结论。"
        message, role = "请你展示资料给我看。", "我同意之后展示资料。"
    elif mode == "defer": message = "先别处理，我们再商量。"
    engine = agreement_engine(description,
        owner="catgirl" if metadata == "owner" else "shared",
        delivery="catgirl_dialogue" if metadata == "owner" else "shared_agreement",
        linked=metadata != "unlinked", source="performance" if metadata == "source" else None)
    node = engine.nodes["start"]
    if metadata == "false_requirement": node["completion_contract"]["all"][0]["equals"] = False
    elif metadata == "story": engine.fact_contract[KEY]["visibility"] = "story"
    elif metadata == "two_requirements":
        other = "scene:start:checked"
        engine.fact_contract[other] = {"value_type": "bool", "visibility": "public", "description": "独立设备核对已完成。"}
        node["completion_contract"]["all"].append({"key": other, "equals": True})
    elif metadata == "ending":
        node["route_gates"][0]["target_node_id"] = next(key for key, value in engine.nodes.items() if value.get("type") == "ending")
    elif metadata == "not_automatic": node["route_gates"][0]["transition_contract"]["advance_on_completion"] = False
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=topic, catgirl_binding=_binding(),
        opening_performance={"performance": "先说说你的安排。", "suggested_inputs": []})
    path = runtime.store._path(topic)
    original = path.read_bytes()
    actor_calls, requests, separate = [], [], []
    eligible = metadata == "valid" and mode not in {"reject_input", "defer", "degraded", "false_op"}
    approve = eligible and mode not in {"refuse", "unclear", "onlyrequest", "actualexperience"}
    propose_actor = eligible

    async def evaluate(self, **kwargs):
        if mode == "degraded": raise ev.NumericV2EvaluatorError("test_evaluator_unavailable")
        return ev.NumericV2EvaluationResult((), False,
            transition_intent="reject" if mode == "reject_input" else "unclear",
            **({"fact_operations": ({"op": "set", "key": KEY, "value": False, "visibility": "public"},),
                "fact_audit": ({"key": KEY, "subject": "双方", "action": "未达成", "object": "安排", "result": "未同意",
                    "evidence": [{"source": "player_input", "quote": message}]},)} if mode == "false_op" else {}))

    async def generate(self, **kwargs):
        assert path.read_bytes() == original
        if kwargs["outcome"].route is None:
            assert KEY not in kwargs["outcome"].session.story_state["facts"] or mode == "false_op"
            assert not kwargs.get("approved_completion_facts") or mode == "false_op"
            actor_calls.append("source")
            return {"scene_narration": "东西仍放在桌上。", "performance": role, "suggested_inputs": [],
                    "fact_candidates": [{"key": KEY, "value": True, "evidence_quote": role}] if propose_actor else []}
        actor_calls.append("tail")
        assert approve and kwargs["transition_source"]["performance"] == role
        if mode == "tail_failure": raise NumericV2ActorOutputError("test_tail_failure")
        return engine.finalize_transition_performance(kwargs["outcome"], {
            "source_performance": role, "source_scene_narration": "东西仍放在桌上。", "bridge_scene_narration": "",
            "target_scene_narration": "两人开始整理。", "target_performance": "按说好的安排继续。", "suggested_inputs": [],
        }, target_opening="两人开始整理。", bridge_required=False)

    async def separate_review(self, **kwargs):
        separate.append(kwargs)
        assert mode == "false_op", "prebound claims must not be resampled through an Actor-only S"
        assert kwargs["claims"][0]["value"] is False
        return ev.NumericV2StoryEvidenceReview((0,))

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            assert path.read_bytes() == original
            data = prompt_payload(messages[1].content)
            requests.append(data)
            payload = contract_audit_payload({"violations": []}, messages)
            schema = kwargs["response_format"]["json_schema"]["schema"]["properties"]
            if "premature_departure" in schema: payload["premature_departure"] = []
            if "route_conflicts" in schema: payload["route_conflicts"] = []
            if "completion_check" in data:
                payload["fact_discoveries"] = [{"target_index": 0, "refs": ["e0",
                    next(row["ref"] for row in data["completion_check"]["sources"] if row.get("field") == "performance")]}] if eligible else []
            if actor_calls[-1] == "source":
                assert ("bound_completion_check" in data) is eligible
                if eligible:
                    check = data["bound_completion_check"]
                    claim, = check["claims"]
                    assert claim["key"] == KEY and claim["value"] is True and claim["description"] == description
                    assert {"source": "player_input", "quote": message} in claim["evidence"]
                    assert any(row["source"] == "actor_performance" and role in row["quote"] for row in claim["evidence"])
                    bound_sources = {row["ref"]: row for row in check["evidence_source_table"]}
                    proof = [bound_sources[ref] for ref in check["evidence_bindings"][0]["refs"]]
                    assert [row["kind"] for row in proof] == ["current_player", "current_draft"]
                    payload["bound_completion_approved_indexes"] = [0] if approve else []
            else:
                assert "bound_completion_check" not in data
                if mode == "tail_contract_failure":
                    payload["author_checks"]["0"] = "uncertain"
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", separate_review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "沉着。")
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("shared-source", 0, message), ensure_current_binding=lambda _: _binding())
    if mode in {"tail_failure", "tail_contract_failure"}:
        with pytest.raises(NumericV2ActorOutputError): await wf._execute_numeric_v2_turn(**kwargs)
        assert path.read_bytes() == original
        return
    result = await wf._execute_numeric_v2_turn(**kwargs)
    assert result.stored.session.revision == 1
    assert result.stored.session.current_node_id == ("work" if approve else "start")
    if mode == "false_op": assert result.stored.session.story_state["facts"][KEY]["value"] is False
    else: assert (KEY in result.stored.session.story_state["facts"]) is approve
    assert len([row for row in result.stored.ledger_events if row["from_node_id"] != row["to_node_id"]]) == int(approve)
    assert len(requests) == 1 + int(approve) and len(separate) == int(mode == "false_op")


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["equipment", "archive"])
@pytest.mark.parametrize("mode", ["agree", "refuse", "unclear", "onlyrequest", "actualexperience"])
async def test_prebound_joint_claim_keeps_player_and_role_evidence_without_extra_s(tmp_path, monkeypatch, topic, mode):
    await _exercise(tmp_path, monkeypatch, mode=mode, topic=topic)


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", ["owner", "unlinked", "source", "story", "false_requirement", "two_requirements", "ending", "not_automatic"])
async def test_metadata_is_a_narrow_scheduling_condition_not_fact_authority(tmp_path, monkeypatch, metadata):
    await _exercise(tmp_path, monkeypatch, metadata=metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reject_input", "defer", "degraded", "false_op"])
async def test_existing_rejection_degradation_or_op_is_not_overridden(tmp_path, monkeypatch, mode):
    await _exercise(tmp_path, monkeypatch, mode=mode)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["tail_failure", "tail_contract_failure"])
async def test_shared_preapproval_remains_uncommitted_on_later_failure(tmp_path, monkeypatch, mode):
    await _exercise(tmp_path, monkeypatch, mode=mode)

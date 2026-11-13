"""Completion facts follow their actual player or Actor evidence before routing."""

from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload

import pytest

from services.theater import numeric_v2_evaluator as evaluator, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for
from tests.utils.theater_prompt import prompt_payload


KEY = "scene:start:done"
# This regression exercises the actual narrow-audit adapter with an offline transport.
_VERIFY_CONTRACT_BOUNDARIES = evaluator.NumericV2MetricEvaluator.verify_contract_boundaries
_REVIEW_STORY_EVIDENCE = evaluator.NumericV2MetricEvaluator.review_story_evidence


def _story(*, performance=False, route_choice=False):
    story = story_for("检修间")
    source, middle = story["nodes"][0], story["nodes"][2]
    middle["route_gates"][0]["conditions"] = deepcopy(source["route_gates"][1]["conditions"])
    middle["route_gates"].append(source["route_gates"][0])
    source["route_gates"] = [source["route_gates"][1]]
    if performance:
        story["fact_contract"]["facts"][KEY]["evidence_source"] = "performance"
    if route_choice:
        choice = "scene:start:choice"
        story["fact_contract"]["facts"][choice] = {
            "value_type": "bool", "visibility": "public", "description": "玩家选定前往检修间。"}
        source["route_gates"][0]["conditions"]["all"].append({
            "type": "fact_equals", "key": choice, "value": True})
    return story


def _pending_departure_story(*, author_boundary=True):
    story = _story(performance=True)
    source = story["nodes"][0]
    source.update(min_turns=1, recommended_turns=1)
    if author_boundary:
        source["story_beat"]["must_not_happen"] = ["本幕不得销毁登记单。"]
    contract = source["route_gates"][0]["transition_contract"]
    contract.pop("fallback_offer")
    contract.pop("accept_input")
    contract.update(advance_on_completion=True, reason="前往检修间开展下一阶段校准。")
    return story


def test_blocked_actor_prompt_prioritizes_local_delivery_over_exit_diagnosis():
    story = _pending_departure_story()
    direction_summary = "本幕收束时双方已完成检查，离开站台前往检修间。"
    story["nodes"][0]["story_beat"].update(
        opening_scene="设备摆在站台桌面上。", narrative_summary=direction_summary,
    )
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="blocked_prompt", catgirl_binding=_binding(), opening_performance=_opening())
    message = "我已经到检修间了。"
    turn = TurnRequestV2("inspect", 0, message)
    outcome = engine.resolve_turn(session, turn, ())
    assert outcome.departure_blocker == "completion_pending"
    diagnostic = "玩家声称到达目标，去向未获批准。"
    direction = "检查眼前设备的接线并读出仪表结果。"
    system, human = _turn_messages(engine, session, outcome, message, "认真", "猫娘", "你",
        exit_mismatch_reason=diagnostic, progress_direction=direction, retry_hint="从本幕重新写。",
        departure_attempt_observed=True)
    data = prompt_payload(human.content)
    assert data["player_input"] == message and data["current_scene_task"] == {"direction": direction}
    assert direction not in data["player_input_scope"]
    assert diagnostic not in human.content
    assert direction_summary not in human.content and "设备摆在站台桌面上。" in data["current_scene"]
    assert "本轮舞台锁定" in system.content and "再次问同一选择" in system.content
    assert "待确认的跨幕行动只商定做法" not in data["pacing"]
    assert "按最近已播正文交付可演的幕内变化" in data["pacing"]


@pytest.mark.asyncio
async def test_deterministic_departure_repair_keeps_target_excerpt_in_audit(tmp_path, monkeypatch):
    story = _pending_departure_story()
    target_id = story["nodes"][0]["route_gates"][0]["target_node_id"]
    target = next(node for node in story["nodes"] if node["id"] == target_id)
    target["story_beat"]["opening_scene"] = "检修间的仪表开始自动记录最终校准结果。"
    leaked = target["story_beat"]["opening_scene"]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="deterministic_departure", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    actor_calls = []
    reply = "（转过眼前的仪表）我们先看看接线的读数。"

    async def evaluate(self, **kwargs):
        return wf.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        actor_calls.append(kwargs)
        if len(actor_calls) == 1:
            return {"scene_narration": leaked, "performance": "我们来看看读数。", "suggested_inputs": []}
        assert kwargs["player_input"] == leaked
        assert leaked.rstrip("。") not in kwargs["retry_hint"] and kwargs.get("draft_repair_context") is None
        return {"performance": reply, "suggested_inputs": []}

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "认真。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("inspect", 0, leaked), ensure_current_binding=lambda _: _binding())
    assert len(actor_calls) == 2 and result.performance["performance"] == reply
    assert result.stored.session.current_node_id == "start"
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.parametrize("performance,route_choice,combined", [
    (False, False, False), (False, True, False), (True, False, True), (True, True, False),
])
def test_completion_source_controls_whether_actor_can_evaluate_progress(monkeypatch, performance, route_choice, combined):
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    engine = NumericV2Engine.from_mapping(_story(performance=performance, route_choice=route_choice))
    session = engine.create_session(session_id="ownership", catgirl_binding=_binding(), opening_performance=_opening())
    current = SimpleNamespace(session=session, ledger_events=())
    assert wf._can_combine_ordinary_turn(SimpleNamespace(engine=engine), current,
        TurnRequestV2("answer", 0, "好，照这个安排。"), {"evaluator": True}) is combined


@pytest.mark.asyncio
async def test_agreement_is_registered_before_actor_without_inventing_a_body_quote(tmp_path, monkeypatch):
    story = _story()
    story["fact_contract"]["facts"][KEY]["description"] = "玩家同意由双方协作检查设备。"
    story["fact_contract"]["facts"][KEY]["evidence_source"] = "player_input"
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="agreement", catgirl_binding=_binding(), opening_performance=_opening())
    before = deepcopy(current.session.to_dict())
    message = "好，我同意一起检查设备。"
    calls = []

    async def evaluate(self, **kwargs):
        calls.append("evaluator")
        return wf._parse_evaluation_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [{"op": "set", "key": KEY, "value": True, "visibility": "public",
                "confidence": "confirmed", "subject": "player", "action": "agree", "object": "inspection",
                "result": "agreed", "evidence": [{"source": "player_input", "quote": message}]}]}),
            runtime.engine, message, current.session)

    async def generate(self, **kwargs):
        assert calls == ["evaluator"] and not kwargs.get("combined_evaluation")
        assert kwargs["outcome"].session.story_state["facts"][KEY]["value"] is True
        calls.append("actor")
        return {"performance": "好，下个整点按约定汇合。", "suggested_inputs": []}

    async def contract(*args, **kwargs): return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然坦率。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("answer", 0, message), ensure_current_binding=lambda _: _binding())
    assert calls == ["evaluator", "actor"]
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert current.session.to_dict() == before
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    assert restored.session.story_state == result.stored.session.story_state


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["request_only", "actor_delivery", "actor_pending", "player_action"])
async def test_completion_is_settled_at_the_stage_that_has_its_evidence(tmp_path, monkeypatch, source):
    story = _pending_departure_story()
    definition = story["fact_contract"]["facts"][KEY]
    definition.pop("evidence_source")  # Legacy packages may omit ownership metadata.
    player_action = source == "player_action"
    definition["description"] = "玩家已拆下旧接头。" if player_action else "角色已向玩家显示完整检修读数。"
    if player_action:
        definition["evidence_source"] = "player_input"
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="completion_stage", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    message = "（用扳手卸下旧接头）旧接头拆下来了。" if player_action else "把完整的检修读数展示给我看吧。"
    delivery = ("（把手放在仪表上，尚未向你展示读数）" if source == "actor_pending"
                else "（把仪表转向你，屏上清楚显示电压十二伏、电流两安，读数尚未到红线）")
    calls = []
    original_bytes = runtime.store._path(current.session.session_id).read_bytes()
    source_body = (delivery + "先确认接线。" if source in {"actor_delivery", "actor_pending"}
                   else "我这就调出读数。")

    async def evaluate(self, **kwargs):
        calls.append("evaluator")
        return wf._parse_evaluation_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [{"op": "set", "key": KEY, "value": True, "visibility": "public",
                "confidence": "confirmed", "subject": "执行人", "action": "操作", "object": "设备",
                "result": "已完成", "evidence": [{"source": "player_input", "quote": message}]}]}),
            engine, message, current.session)

    async def generate(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == original_bytes
        assert kwargs["player_input"] == message
        prepared = kwargs["outcome"]
        if prepared.route is not None:
            assert source in {"actor_delivery", "player_action"}
            assert prepared.session.story_state["facts"][KEY]["value"] is True
            if player_action:
                assert calls == ["evaluator", "player_input"]
                assert kwargs.get("transition_source") is None
                source_response = "旧接头放在托盘里就好。"
                calls.append("actor")
            else:
                assert calls == ["evaluator", "source", "actor_performance"]
                assert kwargs["transition_source"] == {"performance": source_body, "suggested_inputs": []}
                source_response = kwargs["transition_source"]["performance"]
                calls.append("tail")
            assert prepared.route is not None
            return {"segments": [
                {"phase": "source_response", "performance": source_response},
                {"phase": "transition_bridge", "scene_narration": ""},
                {"phase": "target_opening", "scene_narration": "两人来到检修间。",
                 "performance": "新的接头在工作台上。"},
            ], "suggested_inputs": [], "transition_delivered": True, "visible_node_id": prepared.session.current_node_id}
        assert calls == ["evaluator"] and not player_action
        assert KEY not in prepared.session.story_state["facts"]
        assert prepared.route is None and prepared.departure_blocker == "completion_pending"
        calls.append("source")
        if source in {"actor_delivery", "actor_pending"}:
            return {"performance": source_body, "suggested_inputs": [],
                    "fact_candidates": [{"key": KEY, "value": True, "evidence_quote": delivery}]}
        return {"performance": source_body, "suggested_inputs": []}

    class EvidenceClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            system, human = messages
            assert "目标要求实际交付、执行或完整体验时" in system.content
            assert runtime.store._path(current.session.session_id).read_bytes() == original_bytes
            wire = prompt_payload(human.content)
            claim, = wire["claims"]
            evidence = claim["evidence"]
            if player_action:
                assert calls == ["evaluator"]
                assert evidence == [{"source": "player_input", "quote": message}]
                assert not wire["actor_evidence"]
                calls.append("player_input")
                approved = [0]
            elif len(evidence) == 1:
                assert source in {"actor_delivery", "actor_pending"}
                assert calls == ["evaluator", "source"]
                assert evidence == [{"source": "actor_performance", "quote": delivery}]
                assert wire["actor_evidence"] == source_body
                calls.append("actor_performance")
                approved = [0] if source == "actor_delivery" else []
            else:
                assert source in {"request_only", "actor_pending"}
                assert calls == (["evaluator", "source"] if source == "request_only"
                                 else ["evaluator", "source", "actor_performance"])
                assert evidence == [{"source": "player_input", "quote": message},
                                    {"source": "actor_performance", "quote": source_body}]
                assert wire["actor_evidence"] == source_body
                calls.append("composite_evidence")
                # A request plus a promise or still-pending display is not actual delivery.
                approved = []
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"approved_indexes": approved}, messages)))

    async def model_config(_):
        return {"model": "offline", "base_url": "https://example.invalid/v1"}

    async def create_client(*args, **kwargs):
        return EvidenceClient()

    async def contract(*args, **kwargs):
        return ()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", _REVIEW_STORY_EVIDENCE)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然坦率。")
    monkeypatch.setattr(evaluator, "_model_config", model_config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", create_client)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("inspect", 0, message), ensure_current_binding=lambda _: _binding())
    stored = await runtime.restore_session(current.session.session_id)
    assert stored == result.stored
    assert (KEY in stored.session.story_state["facts"]) == (source in {"actor_delivery", "player_action"})
    assert (stored.session.current_node_id != "start") == (source in {"actor_delivery", "player_action"})
    transitions = [row for row in stored.ledger_events if row["from_node_id"] != row["to_node_id"]]
    assert len(transitions) == int(source in {"actor_delivery", "player_action"})
    if source == "actor_delivery":
        assert calls == ["evaluator", "source", "actor_performance", "tail"]
        assert result.performance["segments"][0]["performance"] == source_body
    elif source == "actor_pending":
        assert calls == ["evaluator", "source", "actor_performance", "composite_evidence"]
        assert result.performance["performance"] == source_body
    elif player_action:
        assert calls == ["evaluator", "player_input", "actor"]
    else:
        assert calls == ["evaluator", "source", "composite_evidence"]
        assert result.performance["performance"] == source_body


@pytest.mark.asyncio
@pytest.mark.parametrize("draft_case", [
    "local", "clear", "prospective", "grounded_recap", "generic_only", "replace", "reverse",
    "same_citation", "covered_citation", "replace_cleared", "disagreement",
    "unrelated_clearance", "unsupported_recap", "rollback",
])
async def test_pending_automatic_departure_only_requests_advice_for_a_repair(tmp_path, monkeypatch, draft_case):
    audit_draft = draft_case != "local"
    confirmed_draft = draft_case in {"replace", "reverse", "same_citation", "covered_citation", "replace_cleared"}
    repair_draft = confirmed_draft or draft_case in {"disagreement", "unrelated_clearance", "unsupported_recap", "rollback"}
    grounded_recap = draft_case == "grounded_recap"
    contextual_clearance = draft_case in {"grounded_recap", "prospective"}
    story = _pending_departure_story(author_boundary=audit_draft)
    route = story["nodes"][0]["route_gates"][0]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="pending_departure", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    prior = runtime.prepare_turn(current, TurnRequestV2("first", 0, "我们先看看设备。"), ())
    previous_quote = "连接状态检查已完成。"
    previous_performance = "设备的外壳还没打开。" + (previous_quote if grounded_recap else "")
    current = await runtime.commit_turn(prior, {"performance": previous_performance, "suggested_inputs": []})
    assert engine.preview_route("start", current.session.metrics)["id"] == route["id"]
    assert engine.completion_contract_satisfied(current.session) is False
    directions = []
    review_calls = []
    contract_calls = []
    disagreements = []
    early = "设备已经启动，下一阶段的校准也完成了。"
    future_result = "下一阶段的校准也完成了。"
    first_citation = "校准也完成了。" if draft_case == "covered_citation" else future_result
    still_early = "设备重新启动，检修间的下一阶段校准也结束了。"
    action = "伸手指向设备边缘的指示灯"
    weak_report = "设备边缘的指示灯"
    local_action = f"（{action}）" if audit_draft else ""
    local = {
        "grounded_recap": "说回刚才的记录，连接状态检查已经完成。",
        "prospective": "如果连接状态确认无误，我们再去检修间校准。",
    }.get(draft_case, "我先检查设备的连接状态。")

    async def evaluate(self, **kwargs):
        return wf.NumericV2EvaluationResult((), False)

    async def review(self, **kwargs):
        assert not kwargs["claims"] and kwargs["review_progress"] and kwargs["blocked_departure"]
        review_calls.append(kwargs["message"])
        return wf.NumericV2StoryEvidenceReview(direction="当场检查设备接线。")

    async def generate(self, **kwargs):
        directions.append(kwargs.get("progress_direction"))
        assert kwargs["outcome"].session.current_node_id == "start"
        first = len(directions) == 1
        assert bool(kwargs.get("retry_hint")) is not first
        if not first:
            assert "从最近已提交的本幕状态和玩家本轮原话重新写" in kwargs["retry_hint"]
            assert kwargs.get("draft_repair_context") is None
            assert future_result not in kwargs["retry_hint"] and weak_report not in kwargs["retry_hint"]
        text = local
        if repair_draft and first:
            text = early
        elif draft_case == "rollback":
            text = still_early
        return {"performance": local_action + text, "suggested_inputs": []}

    async def check_contract(self, **kwargs):
        if not audit_draft:
            return ()
        assert "本轮" in kwargs["exit_mismatch_reason"]
        assert "检修间" not in kwargs["exit_mismatch_reason"]
        assert "检修间" in kwargs["departure_plan"]["transition_direction"]
        assert kwargs["departure_plan"]["opening_reference"] == "雨停后的长街恢复了安静。"
        first_premature_draft = kwargs["actor_performance"]["performance"] == local_action + early
        if first_premature_draft:
            assert kwargs["author_boundaries_only"] is True
        problems = await _VERIFY_CONTRACT_BOUNDARIES(self, **kwargs)
        diagnostics = kwargs["diagnostics"]
        disagreements.append(diagnostics.get("premature_departure_disagreement") is True)
        if first_premature_draft:
            issue, = diagnostics["findings"]
            assert issue["quote"] == (weak_report if draft_case == "reverse" else future_result)
            assert issue["candidate"]["id"] == ("c0" if draft_case == "reverse" else "c1")
            assert issue["prior"]["id"] == "d0"
            assert diagnostics["premature_departure_confirmed"] is confirmed_draft
            if draft_case == "reverse":
                assert issue["first_report"] == {"candidate_ref": "c1", "quote": future_result}
            elif draft_case == "replace":
                assert issue["first_report"] == {"candidate_ref": "c0", "quote": weak_report}
            else:
                assert "first_report" not in issue
        return problems

    class ContractClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            wire = prompt_payload(messages[1].content)
            if "finding" in wire:
                contract_calls.append("confirmation")
                assert draft_case == "generic_only"
                assert wire["finding"]["rule"] == "本幕不得销毁登记单。"
                assert "departure_sources" not in wire
                return SimpleNamespace(content=json.dumps(contract_audit_payload(json.loads('{"applicable":true,"confirmed":false}'), messages)))
            if "findings" in wire:
                contract_calls.append("confirmation")
                findings = wire["findings"]
                departure, = [item for item in findings if (item["prior"] or {}).get("id") == "d0"]
                ordinary, = [item for item in findings if item is not departure]
                assert ordinary["rule"] == "本幕不得销毁登记单。"
                candidates = {item["id"]: item for item in wire["departure_sources"]}
                assert candidates["d0"]["departure_blocker"] == "completion_pending"
                assert "检修间" not in candidates["d0"]["text"]
                assert "检修间" in wire["departure_plan"]["transition_direction"]
                text = candidates["c1"]["text"]
                quote = text.partition("，")[2]
                use_local_quote = (text == local and not contextual_clearance) or draft_case in {"replace", "replace_cleared"}
                assert departure["candidate"]["id"] == ("c0" if use_local_quote else "c1")
                if use_local_quote:
                    assert departure["quote"] == weak_report
                else:
                    assert departure["quote"] == (first_citation if text == early else quote)
                replacement = []
                clearance = []
                if grounded_recap:
                    clearance = [{"reason": "recap", "quote": text, "prior_quote": previous_quote}]
                elif draft_case == "prospective":
                    clearance = [{"reason": "prospective", "quote": text, "prior_quote": ""}]
                elif text == local or draft_case == "unrelated_clearance":
                    clearance = [{"reason": "current_scene", "quote": action, "prior_quote": ""}]
                elif draft_case == "unsupported_recap":
                    clearance = [{"reason": "recap", "quote": text, "prior_quote": quote}]
                elif confirmed_draft:
                    replacement = ([{"candidate_ref": "c0", "quote": weak_report}] if draft_case == "reverse"
                                   else [{"candidate_ref": "c1", "quote": quote}])
                    if draft_case == "replace_cleared":
                        clearance = [{"reason": "current_scene", "quote": action, "prior_quote": ""}]
                return SimpleNamespace(content=json.dumps(contract_audit_payload({
                    "verdicts": [{"applicable": True, "confirmed": False} for _ in findings],
                    "premature_departure": replacement,
                    "departure_clearance": clearance,
                }, messages), ensure_ascii=False))
            contract_calls.append("draft")
            sources = {item["id"]: item for item in wire["sources"]}
            assert sources["d0"]["transition_authorized"] is False
            assert sources["d0"]["departure_blocker"] == "completion_pending"
            assert "检修间" not in sources["d0"]["text"]
            assert "检修间" in wire["departure_plan"]["transition_direction"]
            assert sources["c0"]["text"] == action
            text = sources["c1"]["text"]
            assert text in {early, still_early, local}
            rule = next(item for item in wire["author_boundaries"] if "销毁登记单" in item["text"])
            departure = []
            if draft_case != "generic_only":
                if (text == local and not contextual_clearance) or draft_case in {"replace", "replace_cleared"}:
                    departure = [{"candidate_ref": "c0", "quote": weak_report}]
                else:
                    departure = [{"candidate_ref": "c1", "quote": first_citation if text == early else text.partition("，")[2]}]
            return SimpleNamespace(content=json.dumps(contract_audit_payload({
                "violations": [{"rule_index": rule["rule_index"], "candidate_ref": "c1",
                                "quote": text.partition("，")[0], "prior_ref": ""}],
                "premature_departure": departure,
            }, messages), ensure_ascii=False))

    async def model_config(_):
        return {"model": "offline", "base_url": "https://example.invalid/v1"}

    async def create_contract_client(*args, **kwargs):
        return ContractClient()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", check_contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然坦率。")
    monkeypatch.setattr(evaluator, "_model_config", model_config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", create_contract_client)

    turn = TurnRequestV2("check", 1, "你能先检查连接状态吗？")
    if draft_case == "rollback":
        with pytest.raises(wf.NumericV2ActorOutputError, match="numeric_v2_contract_violation"):
            await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
                turn=turn, ensure_current_binding=lambda _: _binding())
        assert await runtime.restore_session(current.session.session_id) == current
    else:
        result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=turn, ensure_current_binding=lambda _: _binding())
        assert result.performance["performance"] == local_action + local
        assert result.stored.session.performance_history[-1]["performance"] == local_action + local
        assert early not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
        assert result.stored.session.current_node_id == "start"
        assert await runtime.restore_session(current.session.session_id) == result.stored

    assert len(review_calls) == int(repair_draft)
    assert directions == ([None, "当场检查设备接线。"] if repair_draft else [None])
    assert contract_calls == ["draft", "confirmation"] * (len(directions) if audit_draft else 0)
    assert disagreements == {
        "local": [], "clear": [False], "prospective": [False], "grounded_recap": [False], "generic_only": [False],
        "replace": [False, False], "reverse": [False, False], "same_citation": [False, False], "covered_citation": [False, False],
        "replace_cleared": [False, False], "disagreement": [True, False], "unrelated_clearance": [True, False],
        "unsupported_recap": [True, False], "rollback": [True, True],
    }[draft_case]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "ordinary", "local", "prospective", "repair", "rollback", "cleared_but_unresolved", "cleared_with_scope",
    "partial_primary_scope", "partial_confirmation_scope", "confirmation_timeout", "complete_mixed_scope",
    "author_confirmation",
    "no_delivery",
])
async def test_blocked_departure_requires_scope_only_on_high_risk_turns(tmp_path, monkeypatch, case):
    story = _pending_departure_story()
    if case == "no_delivery":
        story["nodes"][0]["story_beat"]["fixed_narrations"] = [{
            "id": "equipment_entry", "text": "仪表摆在设备旁。", "trigger": {"type": "entry"},
            "after": [], "required_before_exit": True,
        }]
        story["nodes"][0]["completion_contract"]["all"].append({"fixed_narration_id": "equipment_entry"})
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="departure_scope", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    prior = runtime.prepare_turn(current, TurnRequestV2("first", 0, "先看看设备。"), ())
    current = await runtime.commit_turn(prior, {"performance": "设备的外壳还没打开。", "suggested_inputs": []})
    high_risk = case != "ordinary"
    local = "仪表的指针随演示键摆动，读数就在这里。" if case == "no_delivery" else "我先检查设备的连接状态。"
    prospective = "如果检查通过，我们再去检修间校准。"
    early = "检修间的校准已经完成了。"
    local_action = "伸手检查设备接线"
    mixed_scope = f"（{local_action}）{prospective}"
    mixed_departure = f"（{local_action}）{early}"
    attempted = "我已经到检修间了。" if high_risk else "设备的连接正常吗？"
    drafts = {
        "ordinary": [local], "local": [local], "prospective": [prospective],
        "repair": [early, local], "rollback": [early, "设备已经在检修间校准完毕。"],
        "cleared_but_unresolved": [mixed_departure, local], "cleared_with_scope": [local],
        "partial_primary_scope": [mixed_departure, local], "partial_confirmation_scope": [mixed_departure, local],
        "confirmation_timeout": [mixed_departure, local],
        "complete_mixed_scope": [mixed_scope],
        "author_confirmation": [local],
        "no_delivery": ["别急着走，检查还没结束。", local],
    }[case]
    actor_calls, contract_calls = [], []
    confirmation_calls = []

    async def evaluate(self, **kwargs):
        result = wf._parse_evaluation_output(json.dumps({
            "scene_complete": False, "metric_changes": {}, "exit_matches": False,
            "exit_comparison": "本轮未获准离场。" if high_risk else "玩家只问本幕设备状态。",
            "departure_attempt_quote": "我已经到检修间了。" if high_risk else "历史中有人说离开。",
        }), runtime.engine, attempted, current.session)
        assert result.departure_attempt_observed == high_risk and result.transition_intent == "unclear"
        return result

    async def review(self, **kwargs):
        assert high_risk and kwargs["review_progress"] and kwargs["blocked_departure"] and not kwargs["claims"]
        return wf.NumericV2StoryEvidenceReview(direction="当场检查设备接线。")

    async def generate(self, **kwargs):
        assert kwargs["outcome"].session.current_node_id == "start"
        assert kwargs["outcome"].departure_blocker == "completion_pending"
        assert kwargs.get("progress_direction") == ("当场检查设备接线。" if high_risk else None)
        assert kwargs.get("draft_repair_context") is None
        hint = kwargs.get("retry_hint", "")
        assert bool(hint) is bool(actor_calls)
        assert early not in hint
        body = drafts[len(actor_calls)]
        actor_calls.append(body)
        return {"performance": body, "suggested_inputs": []}

    class ContractClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            wire = prompt_payload(messages[1].content)
            if "finding" in wire and "departure_sources" not in wire:
                assert case in {"author_confirmation", "no_delivery"}
                assert "visible_order" in wire
                assert "premature_departure" not in messages[0].content
                quote = wire["finding"]["quote"]
                confirmation_calls.append(quote)
                if case == "no_delivery":
                    assert wire["current_scene_task"]["direction"] == "当场检查设备接线。"
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"applicable": True, "confirmed": case == "no_delivery"}, messages)))
            if "departure_sources" in wire:
                assert case not in {"author_confirmation", "no_delivery"}
                assert wire["visible_order"]
                assert "通用verdict填uncertain、clearance为空" in messages[0].content
                assert "先判规则在quote发生时是否适用" not in messages[0].content
                assert any(block["id"] == "c0" for block in wire["departure_sources"])
                finding = wire["finding"]
                quote = finding["quote"]
                confirmation_calls.append(quote)
                expected_quote = local_action if case in {
                    "cleared_but_unresolved", "partial_confirmation_scope", "confirmation_timeout"} else (
                    drafts[len(actor_calls) - 1] if case in {"repair", "rollback"} else local)
                assert finding["candidate"]["id"] == "c0" and quote == expected_quote
                if case == "confirmation_timeout":
                    raise TimeoutError
                scope = [{"scope": "current_scene", "candidate_ref": "c0", "quote": quote}]
                if case in {"repair", "rollback"}:
                    scope[0]["scope"] = "departed"
                elif case == "cleared_but_unresolved":
                    scope.append({"scope": "uncertain", "candidate_ref": "c1", "quote": early})
                return SimpleNamespace(content=json.dumps(contract_audit_payload({
                    "applicable": True, "confirmed": False, "departure_scope": scope,
                }, messages), ensure_ascii=False))
            sources = {item["id"]: item for item in wire["sources"]}
            assert sources["d0"]["departure_blocker"] == "completion_pending"
            if high_risk:
                assert "本轮未获准离场。" not in sources["d0"]["text"]
            body = sources["c0"]["text"]
            contract_calls.append(body)
            verdict = {"violations": [], **({"premature_departure": []} if not high_risk else {})}
            if high_risk:
                refs = "、".join(ref for ref in sources if ref.startswith("c"))
                assert f"全部正文编号依次为 {refs}。" in messages[0].content
                first_cleared_draft = len(contract_calls) == 1 and case in {
                    "cleared_but_unresolved", "cleared_with_scope", "partial_confirmation_scope", "confirmation_timeout"}
                if first_cleared_draft:
                    verdict["departure_scope"] = [{"scope": "departed" if ref == "c0" else "uncertain",
                        "candidate_ref": ref, "quote": row["text"]}
                        for ref, row in sources.items() if ref.startswith("c")]
                    if case != "cleared_with_scope":
                        assert sources["c1"]["text"] == early
                else:
                    scope = {local: "current_scene", local_action: "current_scene", prospective: "prospective"}.get(body)
                    if case == "no_delivery":
                        scope = "current_scene"
                    verdict["departure_scope"] = [{"scope": scope or "departed", "candidate_ref": "c0", "quote": body}]
                    if case == "partial_primary_scope" and len(contract_calls) == 1:
                        verdict["departure_scope"][0]["scope"] = "current_scene"
                    if case == "complete_mixed_scope":
                        verdict["departure_scope"].append({"scope": "prospective", "candidate_ref": "c1", "quote": prospective})
                if case == "author_confirmation":
                    # A generic finding alone must not bypass the missing departure verdict.
                    rule = next(item for item in wire["author_boundaries"] if "销毁登记单" in item["text"])
                    verdict["violations"] = [{"rule_index": rule["rule_index"], "candidate_ref": "c0",
                                               "quote": body, "prior_ref": ""}]
                if case == "no_delivery" and body == drafts[0]:
                    assert wire["current_scene_task"]["direction"] == "当场检查设备接线。"
                    rule = next(item for item in wire["author_boundaries"] if "本轮留幕回应须按" in item["text"])
                    verdict["violations"] = [{"rule_index": rule["rule_index"], "candidate_ref": "c0",
                                               "quote": body, "prior_ref": ""}]
            return SimpleNamespace(content=json.dumps(contract_audit_payload(verdict, messages), ensure_ascii=False))

    async def model_config(_):
        return {"model": "offline", "base_url": "https://example.invalid/v1"}

    async def create_contract_client(*args, **kwargs):
        return ContractClient()

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", _VERIFY_CONTRACT_BOUNDARIES)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然坦率。")
    monkeypatch.setattr(evaluator, "_model_config", model_config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", create_contract_client)

    turn = TurnRequestV2("check", 1, attempted)
    if case == "rollback":
        with pytest.raises(wf.NumericV2ActorOutputError, match="numeric_v2_contract_violation"):
            await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
                turn=turn, ensure_current_binding=lambda _: _binding())
        assert await runtime.restore_session(current.session.session_id) == current
    else:
        result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=turn, ensure_current_binding=lambda _: _binding())
        assert result.performance["performance"] == drafts[-1]
        assert result.stored.session.current_node_id == "start"
        assert await runtime.restore_session(current.session.session_id) == result.stored

    assert actor_calls == drafts
    assert contract_calls == (
        [local_action, local] if case in {
            "cleared_but_unresolved", "partial_primary_scope", "partial_confirmation_scope", "confirmation_timeout"}
        else [local_action] if case == "complete_mixed_scope" else drafts
    )
    assert confirmation_calls == {
        "cleared_but_unresolved": [local_action], "cleared_with_scope": [local],
        "partial_confirmation_scope": [local_action], "confirmation_timeout": [local_action],
        "repair": [early], "rollback": drafts,
        "author_confirmation": [local],
        "no_delivery": [drafts[0]],
    }.get(case, [])

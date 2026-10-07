"""Recover only the current uncommitted candidate, still requiring source verification, formal review and one atomic commit."""

import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate

QUOTE = "左侧走廊通往阅览室，通道已经开放。"


@pytest.mark.parametrize("quote,allowed", [(QUOTE, True), ("作者安排去阅览室。", False), ("", False)])
def test_recovery_requires_actual_public_quote(quote, allowed):
    case = initiation_case()
    payload = dict(offer_present=False, valid=False, body_violations=["scene_boundary"],
                   unsafe_suggestion_indexes=[], missed_initiation=True, public_destination_index=0,
                   player_request_quote=case["message"])
    review = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=case["session"], recovery_evidence=(quote,),
                                               recovery_player_input=case["message"])
    assert review.missed_initiation is allowed
    assert review.public_destination_quote == (QUOTE if allowed else "")
    assert review.body_violations == ("scene_boundary",)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_recovery_boolean_is_not_coerced(value):
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=value, public_destination_index=0)
    with pytest.raises(ev.NumericV2EvaluatorOutputError):
        ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"])


def test_legacy_review_does_not_invent_recovery():
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[])
    result = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"])
    assert not result.missed_initiation and not result.public_destination_quote


@pytest.mark.parametrize("index", [-1, 1, True, "0", None])
def test_invalid_evidence_index_does_not_authorize(index):
    # 布尔值也是Python整数的子类，不能被当作编号；越界或缺失同样不恢复。
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=True, public_destination_index=index, player_request_quote="带路吧。")
    result = ev._parse_transition_judge_output(json.dumps(payload), recovery_session=initiation_case()["session"],
                                               recovery_evidence=(QUOTE,), recovery_player_input="带路吧。")
    assert not result.missed_initiation and not result.public_destination_quote


def test_numbered_evidence_excludes_player_and_candidate():
    case = initiation_case()
    messages, evidence = ev._build_transition_judge_messages(
        case["engine"], case["session"], player_input="带我去新秘密房间。",
        actor_performance={"performance": "新秘密房间已经开放。", "suggested_inputs": []},
        check_missed_initiation=True)
    data = json.loads(messages[1].content.split("：", 1)[1])
    recovery_check = data["missed_initiation_check"]
    assert recovery_check["player_request"] == "带我去新秘密房间。"
    assert recovery_check["required_exit"]["chapter"] == case["target"]
    assert recovery_check["public_destination_evidence"]
    assert all(text in case["session"].opening_performance["performance"]
               for text in recovery_check["public_destination_evidence"])
    assert "新秘密房间" not in str(recovery_check["public_destination_evidence"])
    assert evidence == tuple(recovery_check["public_destination_evidence"])
    assert messages[0].content.startswith("本次先独立核对 JSON 开头的 missed_initiation_check")
    assert "保留原八字段并增加 player_request_quote、missed_initiation 与 public_destination_index" in messages[0].content


def test_recovery_checks_actual_entry_even_when_route_reason_only_names_preparation():
    case = initiation_case()
    route = case["engine"].nodes["start"]["route_gates"][1]
    route["transition_contract"]["reason"] = "一起确认登记信息后继续下一步。"
    messages, _ = ev._build_transition_judge_messages(
        case["engine"], case["session"], player_input="信息没错，现在一起确认吧。",
        actor_performance={"performance": "（点头）登记信息确认好了。", "suggested_inputs": []},
        check_missed_initiation=True,
    )
    data = json.loads(messages[1].content.split("：", 1)[1])
    required = data["missed_initiation_check"]["required_exit"]
    # 补查会触发昂贵的正式演绎，必须先看到接受后真正抵达的地点与阶段。
    assert required["direction"] == "一起确认登记信息后继续下一步。"
    assert required["bridge_boundary"] == data["next_scene_direction"]["bridge_boundary"]
    assert required["opening_boundary"] == data["next_scene_direction"]["opening_boundary"]
    assert "阅览室" in required["bridge_boundary"]
    assert "阅览室" in required["opening_boundary"]


@pytest.mark.asyncio
async def test_recovery_uses_sent_evidence_without_parsing_prompt_prefix(monkeypatch):
    case = initiation_case()
    build_messages = ev._build_transition_judge_messages
    sent_evidence = []

    def build_with_another_prefix(*args, **kwargs):
        messages, evidence = build_messages(*args, **kwargs)
        # 展示前缀不属于恢复协议，编号仍取自同一次装箱的证据。
        data = json.loads(messages[1].content.split("：", 1)[1])
        assert evidence == tuple(data["missed_initiation_check"]["public_destination_evidence"])
        messages[1] = type(messages[1])(content="Review data\n" + json.dumps(data, ensure_ascii=False))
        sent_evidence.extend(evidence)
        return messages, evidence

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            assert messages[1].content.startswith("Review data\n")
            index = next(index for index, text in enumerate(sent_evidence) if QUOTE in text)
            payload = dict(offer_present=False, offer_quote="", valid=False, body_violations=[],
                           unsafe_suggestion_indexes=[], missed_initiation=True,
                           public_destination_index=index, player_request_quote=case["message"])
            return type('Response', (), {'content': json.dumps(payload)})()

    async def model_config(_manager):
        return {'model': 'test', 'base_url': 'http://test.invalid'}

    async def client(*_args, **_kwargs):
        return Client()

    monkeypatch.setattr(ev, '_model_config', model_config)
    monkeypatch.setattr(ev, 'create_chat_llm_async', client)
    monkeypatch.setattr(ev, '_build_transition_judge_messages', build_with_another_prefix)
    review = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=case['engine'], session=case['session'], message=case['message'],
        actor_performance={'performance': '我听到了。', 'suggested_inputs': []},
        check_missed_initiation=True)
    assert review.missed_initiation
    assert review.public_destination_quote == next(text for text in sent_evidence if QUOTE in text)


@pytest.mark.asyncio
@pytest.mark.parametrize("formal_failure", [False, "timeout", "body"])
@pytest.mark.parametrize("recover_after_rewrite", [False, True])
async def test_recovery_restarts_from_original_state_and_commits_only_formal(tmp_path, monkeypatch, formal_failure, recover_after_rewrite):
    # 补查误判仍会被正式复核指出；持续语义否定按用户决策兜底，技术故障仍回滚。
    case = initiation_case(message="我们能去阅览室吗？" if formal_failure == "body" else "带路吧。")
    engine = case["engine"]; runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="recover", catgirl_binding=_binding(),
                                          opening_performance=case["session"].opening_performance)
    actor_nodes = []; reviews = []; evaluations = []
    async def evaluate(self, **kwargs):
        evaluations.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2("trust", 2, "玩家兑现承诺", case["message"]),), False)
    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]; actor_nodes.append(outcome.session.current_node_id)
        assert outcome.session.metrics["trust"] == current.session.metrics["trust"] + 2
        if outcome.session.current_node_id == "start":
            return {"performance": "这份普通候选必须丢弃。", "suggested_inputs": [], "transition_offered": False}
        return engine.finalize_transition_performance(outcome, _candidate(), target_opening="阅览室入口。")
    async def review(self, **kwargs):
        reviews.append(kwargs)
        if kwargs.get("check_missed_initiation"):
            # 普通稿先耗尽改稿额度再补查换幕时，正式稿不能再次改写或再次争议复查。
            if recover_after_rewrite and len(actor_nodes) == 1:
                return ev.NumericV2TransitionOfferReview(False, False, ("author_boundary",), (), "普通稿存在冲突。")
            return ev.NumericV2TransitionOfferReview(False, False, (), (), missed_initiation=True,
                                                     public_destination_quote=QUOTE)
        assert kwargs["public_destination_quote"] == QUOTE
        assert kwargs["session"] == current.session
        if formal_failure == "timeout":
            raise ev.NumericV2EvaluatorError("test_formal_timeout")
        if formal_failure == "body":
            return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), (), "玩家只询问可行性。")
        return ev.NumericV2TransitionOfferReview(False, False, (), ())
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
                  turn=TurnRequestV2("go", 0, case["message"]), ensure_current_binding=lambda _: _binding())
    if formal_failure == "timeout":
        with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_(review_failed|fact_boundary)"):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session("recover") == current
    else:
        result = await workflow.execute_numeric_v2_turn(**kwargs)
        assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
        assert result.stored.session.current_node_id == "ending_leave"
        assert result.diagnostics["missed_initiation_recoveries"] == 1
        assert result.diagnostics["semantic_review_fallback"] is (formal_failure == "body")
        assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
        assert "普通候选必须丢弃" not in str(result.stored)
        assert "public_destination_quote" not in result.stored.session.to_dict()
        assert await NumericV2Runtime(engine, tmp_path).restore_session("recover") == result.stored
    assert len(evaluations) == 1
    assert actor_nodes == (["start", "start", "ending_leave"] if recover_after_rewrite
                           else ["start", "ending_leave", "ending_leave"] if formal_failure == "body"
                           else ["start", "ending_leave"])
    assert len(reviews) == (4 if formal_failure == "body" or recover_after_rewrite else 2)


@pytest.mark.parametrize("request_quote,allowed", [
    ("带路吧。", True), ("带路吧", True), ("  带路吧。  ", True),
    ("", False), ("去阅览室。", False), (QUOTE, False),
    ("稍后再去。", False), (None, False), (True, False), (1, False),
    ([], False), ({"text": "带路吧。"}, False),
])
def test_recovery_requires_quote_from_current_request_and_preserves_other_results(request_quote, allowed):
    case = initiation_case()
    fact = {"key": "scene:start:confirmed", "value": True, "evidence_quote": "确认好了。"}
    payload = dict(offer_present=False, valid=False, body_violations=["player_action"],
                   unsafe_suggestion_indexes=[1], failure_reason="仍需修正正文。",
                   missed_initiation=True, public_destination_index=0,
                   player_request_quote=request_quote, fact_candidates=[fact],
                   approved_evaluator_fact_indexes=[0])
    result = ev._parse_transition_judge_output(
        json.dumps(payload), recovery_session=case["session"], recovery_evidence=(QUOTE,),
        recovery_player_input=case["message"], completion_fact_review=True, evaluator_fact_claim_count=1,
    )
    assert result.missed_initiation is allowed
    assert result.public_destination_quote == (QUOTE if allowed else "")
    assert result.body_violations == ("player_action",)
    assert result.unsafe_suggestion_indexes == (1,)
    assert result.failure_reason == "仍需修正正文。"
    assert result.fact_candidates == (fact,)
    assert result.approved_evaluator_fact_indexes == (0,)


def test_legacy_positive_without_current_request_quote_does_not_recover():
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=True, public_destination_index=0)
    result = ev._parse_transition_judge_output(
        json.dumps(payload), recovery_session=initiation_case()["session"],
        recovery_evidence=(QUOTE,), recovery_player_input="带路吧。",
    )
    assert not result.missed_initiation
    assert not result.public_destination_quote


def test_overlong_current_request_quote_does_not_recover():
    quote = "沿着走廊一直往前走" * 7
    payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                   missed_initiation=True, public_destination_index=0, player_request_quote=quote)
    result = ev._parse_transition_judge_output(
        json.dumps(payload), recovery_session=initiation_case()["session"],
        recovery_evidence=(QUOTE,), recovery_player_input=quote,
    )
    assert not result.missed_initiation


@pytest.mark.asyncio
@pytest.mark.parametrize("place,target", [("档案接待台", "阅览室"), ("花园值班室", "温室")])
async def test_unverified_current_request_never_starts_formal_actor(tmp_path, monkeypatch, place, target):
    case = initiation_case(place, target, message="我先整理一下手里的东西。")
    runtime = NumericV2Runtime(case["engine"], tmp_path)
    current = await runtime.start_session(
        session_id="stay", catgirl_binding=_binding(), opening_performance=case["session"].opening_performance,
    )
    actor_nodes = []
    review_count = 0

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        actor_nodes.append(kwargs["outcome"].session.current_node_id)
        return {"performance": "（点头）慢慢来。", "suggested_inputs": [], "transition_offered": False}

    async def review(self, **kwargs):
        nonlocal review_count
        review_count += 1
        assert kwargs["check_missed_initiation"]
        payload = dict(offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
                       missed_initiation=True, public_destination_index=0, player_request_quote="带路吧。")
        return ev._parse_transition_judge_output(
            json.dumps(payload), recovery_session=kwargs["session"],
            recovery_evidence=(f"左侧走廊通往{target}，通道已经开放。",),
            recovery_player_input=kwargs["message"],
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("stay", 0, case["message"]), ensure_current_binding=lambda _: _binding(),
    )
    assert actor_nodes == ["start"] and review_count == 1
    assert result.diagnostics["missed_initiation_recoveries"] == 0
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.revision == 1 and len(result.stored.ledger_events) == 1
    assert await NumericV2Runtime(case["engine"], tmp_path).restore_session("stay") == result.stored

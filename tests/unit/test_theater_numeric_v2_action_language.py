"""Fixed positive and negative examples of colloquial player actions; real stress tests assess model understanding separately."""

from copy import deepcopy
from dataclasses import replace

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_context import PLAYER_ACTION_EXECUTION_RULE, PLAYER_ACTION_LANGUAGE_RULE
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session


def build_action_language_cases(builder=None):
    """Fix authorization and expected answers first; colloquial or action formatting changes expression without changing scene premises."""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    opening = "登记台有一份白色登记表和一支可用的笔，玩家已看完条款；两者就在手边，登记表还未签署。猫娘刚请玩家签白色登记表；旁边另有一份蓝色表格，留待另行决定。"
    engine.nodes["start"]["story_beat"] = {
        "opening_scene": opening, "summary": opening,
        "must_not_happen": [
            "没有玩家对相应表格的签署行动，不能宣称该表格已签署；签白表不授权签蓝表。",
            "签署登记表不代表柜子已打开，也不授予柜子的钥匙。",
        ],
    }
    engine.nodes["start"]["route_gates"] = []
    session = replace(_session(engine), opening_performance={"scene_narration": opening})
    signed = "白色登记表已签署，蓝色表格仍未动。"
    rows = [
        ("plain_execution", "行，我签。", signed, True),
        ("execution_without_parentheses", "我现在在白色登记表上签字。", signed, True),
        ("preparing_is_not_execution", "我准备签，先再看看条款。", signed, False),
        ("considering_is_not_execution", "我考虑签，但还没决定。", signed, False),
        ("question_is_not_execution", "签完是不是就能领取资料？", signed, False),
        ("conditional_is_not_execution", "等你解释清楚最后一条，我才签。", signed, False),
        ("no_extra_object", "行，我签。", "白色登记表和蓝色表格都已签署。", False),
        ("no_unrelated_result", "行，我签。", "白色登记表已签署，旁边的锁柜也因此打开了。", False),
    ]
    return [{
        "name": name, "group": "action_language", "expected_safe": safe,
        "expected_body_rejected": not safe,
        "messages": [message.content for message in (builder or evaluator._build_transition_judge_messages)(
            engine, session, player_input=player_input,
            actor_performance={"performance": "（看向登记台）我看清了。", "scene_narration": narration, "suggested_inputs": []},
        )[0]],
    } for name, player_input, narration, safe in rows]


def test_action_language_rule_reaches_actor_evaluator_and_guard():
    """Verify that actual packing paths share the contract so another consumer cannot reject Actor output using obsolete semantics."""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    message = "行，我签。"
    outcome = engine.resolve_turn(session, TurnRequestV2("plain_action", 0, message), ())
    groups = [
        actor._turn_messages(engine, session, outcome, message, "安静克制。", "测试猫娘", "哥哥"),
        evaluator._build_messages(engine, session, message),
        evaluator._build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={"performance": "（抬眼）收到。", "suggested_inputs": []})[0],
    ]
    # Ordinary Actor merges the shared prose while keeping each action boundary.
    ordinary = groups[0][0].content
    assert ordinary.count(PLAYER_ACTION_EXECUTION_RULE) == 1
    assert "‘行，我签’" not in ordinary
    assert "current_scene是作者规划，story_so_far是实际历史" in ordinary
    assert "next_scene 只允许未来邀请" in ordinary
    assert all(group[0].content.count(PLAYER_ACTION_LANGUAGE_RULE) == 1 for group in groups[1:])
    assert all("作者角色状态是作者预期的入幕起点" in group[0].content for group in groups[1:])
    assert all("目标幕尚未播放时" in group[0].content for group in groups[1:])
    # Splitting nodes must not invalidate continuous actions; all three consumers must preserve the public acceptance flow for ordinary scene changes.
    assert all("不能仅因作者把该动作安排在下一节点" in group[0].content for group in groups)
    assert all("或玩家明确要求前往已公开的下一地点" in group[0].content for group in groups[1:])
    assert all("角色自然提醒实际情况" in group[0].content for group in groups[1:])
    assert all("不为顺从输入倒退物件状态或补造前置动作" in group[0].content for group in groups[1:])
    assert all(message in group[1].content for group in groups)
    # Semantic guidance does not modify input, Session, or route state, nor commit actions from keywords alone.
    assert session.revision == 0
    assert not session.transition_offered
    # Review violations must use authorization semantics too, so the old unfinished-action definition cannot override shared guidance.
    assert "正文替玩家新增未获授权的行动、决定或回应" in groups[2][0].content
    assert "不要求玩家先复述动作已完成" in groups[2][0].content


def test_post_opening_state_rule_reaches_formal_transition_actor_and_guard():
    # Formal transitions use a separate prompt; target state applies only in the target segment, following the shared timing contract.
    from services.theater.numeric_v2_context import SCENE_ENTRY_STATE_RULE
    from tests.unit.test_theater_numeric_v2_transition_history import _engine, _opening, _candidate
    from tests.unit.test_theater_numeric_v2_runtime import _binding

    engine = _engine()
    session = engine.create_session(session_id="state_phase", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("one", 0, "谢谢。"), (),
                                  scene_complete=True, natural_ending_ready=True)
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening="旧开场。")
    groups = [
        actor._turn_messages(engine, session, outcome, "谢谢。", "克制。", "小岚", "你"),
        evaluator._build_transition_judge_messages(engine, session, player_input="谢谢。",
            actor_performance=candidate, route_changed=True, transition_outcome=outcome)[0],
    ]
    assert all(group[0].content.count(SCENE_ENTRY_STATE_RULE) == 1 for group in groups)
    role_review = evaluator._build_player_action_review_messages(
        engine, session, player_input="谢谢。", actor_performance=candidate,
        transition_outcome=outcome, public_destination_quote="",
        confirmed_acceptance=False, confirmed_silent_acceptance="",
    )
    # Completed continuous actions carry their results into the next scene and must not revert to the author's default unfinished state.
    for messages in (groups[0], role_review):
        assert "不再次要求执行，也不把物件退回操作前" in messages[0].content
        assert "角色自然提醒实际情况" in messages[0].content
    assert "后段承接它，不再重做" in groups[1][0].content
    assert "最新同对象状态优先" in groups[1][0].content
    assert session.revision == 0


def test_action_language_keeps_late_condition_in_current_input():
    """Deliver a long input's final refusal to all three consumers instead of keeping only its initial intention to sign."""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    message = "我有签署意向。" + "先把条款逐项解释清楚，再让我看看相关说明。" * 20 + "不过我只是考虑签，现在还不签。"
    outcome = engine.resolve_turn(session, TurnRequestV2("conditional_action", 0, message), ())
    groups = [
        actor._turn_messages(engine, session, outcome, message, "安静克制。", "测试猫娘", "哥哥"),
        evaluator._build_messages(engine, session, message),
        evaluator._build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={"performance": "（收回笔）那就再看看。", "suggested_inputs": []})[0],
    ]
    assert all(prompt_payload(group[1].content)["player_input"] == message for group in groups)


def test_unextracted_request_reaches_actor_evaluator_and_review_unchanged():
    import json

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    message = "先把箱子里其余几件逐件拿出来给我看看。我听完再决定。"
    outcome = engine.resolve_turn(session, TurnRequestV2("show_request", 0, message), ())
    projection = outcome.ledger_event["player_action_projection"]
    assert projection["confirmed_actions"] == []
    groups = [
        actor._turn_messages(engine, session, outcome, message, "克制。", "测试猫娘", "哥哥"),
        evaluator._build_messages(engine, session, message),
        evaluator._build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={"performance": "（打开箱盖）先看看这些。", "suggested_inputs": []})[0],
    ]
    assert all(prompt_payload(group[1].content)["player_input"] == message for group in groups)
    actor_projection = json.JSONDecoder().raw_decode(groups[0][0].content.split(
        "本轮玩家动作结果投影（仅为约束，不是新增剧情）：", 1,
    )[1])[0]
    assert actor_projection["confirmed_actions"] == []
    assert all(prompt_payload(group[1].content)["player_action_projection"]["confirmed_actions"] == []
               for group in groups[1:])


def test_player_input_evidence_references_keep_actions_and_future_agreements():
    import json

    from services.theater.numeric_v2_action_projection import (
        player_action_projection_for_prompt, project_player_action_result, reference_player_action_input,
    )

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    message = "（起身走出门）我改天再来。" + "书签我先收在外套口袋里，回头再整理。" * 7
    outcome = engine.resolve_turn(session, TurnRequestV2("leave_and_return", 0, message), ())
    stored = deepcopy(outcome.ledger_event["player_action_projection"])
    assert stored["confirmed_actions"][0]["evidence_quote"] == message
    assert stored["future_references"][0]["evidence_quote"] == message
    groups = [
        actor._turn_messages(engine, session, outcome, message, "安静克制。", "测试猫娘", "哥哥"),
        evaluator._build_messages(engine, session, message),
        evaluator._build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={"performance": "（抬眼）知道了。", "suggested_inputs": []})[0],
    ]
    for group, expected_refs in zip(groups, (1, 2, 2), strict=True):
        assert sum(item.content.count(message) for item in group) == 1
        assert sum(item.content.count('"evidence_ref":"player_input"') for item in group) == expected_refs
        assert "evidence_ref 取 player_input 原话。" in group[0].content
        assert prompt_payload(group[1].content)["player_input"] == message
    actor_projection = json.JSONDecoder().raw_decode(groups[0][0].content.split(
        "本轮玩家动作结果投影（仅为约束，不是新增剧情）：", 1,
    )[1])[0]
    assert actor_projection["confirmed_actions"] == []
    assert "player_left_current_scene" not in actor_projection
    assert actor_projection["future_references"] == [{
        "kind": "future_return_or_follow_up", "status": "future", "source": "player_input",
        "evidence_ref": "player_input",
    }]
    assert outcome.ledger_event["player_action_projection"] == stored

    partial = "（拿起桌边钥匙）窗外起雾了。"
    projection = project_player_action_result(partial)
    assert projection["confirmed_actions"][0]["evidence_quote"] != partial
    prompt_projection = player_action_projection_for_prompt(projection)
    assert reference_player_action_input(prompt_projection, partial) == ""
    assert prompt_projection == projection


def test_action_language_control_pairs_keep_identical_author_facts():
    """Change only execution intent or unauthorized results in counterexamples, avoiding apparent improvements caused by different premises."""

    import json

    cases = build_action_language_cases()
    assert len(cases) == 8
    payloads = [prompt_payload(case['messages'][1]) for case in cases]
    assert all(payload["current_scene"] == payloads[0]["current_scene"] for payload in payloads)
    assert all(payload["scene_context"] == payloads[0]["scene_context"] for payload in payloads)
    assert all("expected_safe" not in case["messages"][1] for case in cases)

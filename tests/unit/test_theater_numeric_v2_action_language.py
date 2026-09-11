"""Fixed positive and negative examples of colloquial player actions; real stress tests assess model understanding separately."""

from dataclasses import replace

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_context import PLAYER_ACTION_LANGUAGE_RULE
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
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
        )],
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
            actor_performance={"performance": "（抬眼）收到。", "suggested_inputs": []}),
    ]
    assert all(group[0].content.count(PLAYER_ACTION_LANGUAGE_RULE) == 1 for group in groups)
    # 演员、判定和复核采用同一状态时点，同时保留未来目标幕与已提交历史的区别。
    assert all("作者角色状态记录开场演完后的起点" in group[0].content for group in groups)
    assert all("目标幕尚未播放时" in group[0].content for group in groups)
    # 连续动作不因分节点而失效；三类消费者仍须保留普通换幕的公开接受流程。
    assert all("不能仅因作者把该动作安排在下一节点" in group[0].content for group in groups)
    assert all("或玩家明确要求前往已公开的下一地点" in group[0].content for group in groups)
    # 输入与事实矛盾时须提醒；不能把正常的新动作和事后改写历史混淆。
    assert all("角色自然提醒实际情况" in group[0].content for group in groups)
    assert all("不为顺从输入倒退物件状态或补造前置动作" in group[0].content for group in groups)
    assert all(message in group[1].content for group in groups)
    # 语义说明不改变输入、Session 或路线状态，也不根据关键词自行提交动作。
    assert session.revision == 0
    assert not session.transition_offered
    # 复核的违规标准也必须采用授权语义，避免共享说明被旧的“未写完动作”定义抵消。
    assert "正文替玩家新增未获授权的行动、决定或回应" in groups[2][0].content
    assert "不要求玩家先复述动作已完成" in groups[2][0].content


def test_post_opening_state_rule_reaches_formal_transition_actor_and_guard():
    # 正式转场使用独立 Prompt，目标状态只能在目标段成立，不能漏用共享时点合同。
    from services.theater.numeric_v2_context import SCENE_ENTRY_STATE_RULE
    from tests.unit.test_theater_numeric_v2_transition_history import _engine, _opening, _candidate
    from tests.unit.test_theater_numeric_v2_runtime import _binding

    engine = _engine()
    session = engine.create_session(session_id="state_phase", catgirl_binding=_binding(), opening_performance=_opening())
    outcome = engine.resolve_turn(session, TurnRequestV2("one", 0, "谢谢。"), (),
                                  scene_complete=True, natural_ending_ready=True)
    candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening="旧开场。")
    groups = [
        actor._turn_messages(engine, session, outcome, "谢谢。", "克制。", "小岚", "你", deterministic_transition=True),
        evaluator._build_transition_judge_messages(engine, session, player_input="谢谢。",
            actor_performance=candidate, route_changed=True, transition_outcome=outcome),
    ]
    assert all(group[0].content.count(SCENE_ENTRY_STATE_RULE) == 1 for group in groups)
    # 已完成的连续动作进入下一幕后只承接结果，不能按作者默认未完成状态回退。
    assert all("不再次要求执行，也不把物件退回操作前" in group[0].content for group in groups)
    assert all("角色自然提醒实际情况" in group[0].content for group in groups)
    assert session.revision == 0


def test_action_language_keeps_late_condition_in_current_input():
    """Deliver a long input's final refusal to all three consumers instead of keeping only its initial intention to sign."""

    import json

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = _session(engine)
    message = "我有签署意向。" + "先把条款逐项解释清楚，再让我看看相关说明。" * 20 + "不过我只是考虑签，现在还不签。"
    outcome = engine.resolve_turn(session, TurnRequestV2("conditional_action", 0, message), ())
    groups = [
        actor._turn_messages(engine, session, outcome, message, "安静克制。", "测试猫娘", "哥哥"),
        evaluator._build_messages(engine, session, message),
        evaluator._build_transition_judge_messages(engine, session, player_input=message,
            actor_performance={"performance": "（收回笔）那就再看看。", "suggested_inputs": []}),
    ]
    assert all(json.loads(group[1].content.split("：", 1)[1])["player_input"] == message for group in groups)


def test_action_language_control_pairs_keep_identical_author_facts():
    """Change only execution intent or unauthorized results in counterexamples, avoiding apparent improvements caused by different premises."""

    import json

    cases = build_action_language_cases()
    assert len(cases) == 8
    payloads = [json.loads(case["messages"][1].split("：", 1)[1]) for case in cases]
    assert all(payload["current_scene"] == payloads[0]["current_scene"] for payload in payloads)
    assert all(payload["scene_context"] == payloads[0]["scene_context"] for payload in payloads)
    assert all("expected_safe" not in case["messages"][1] for case in cases)

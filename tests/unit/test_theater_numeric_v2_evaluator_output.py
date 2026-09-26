"""Evaluator accepts a complete JSON fence without repairing ambiguous output."""

from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluatorOutputError,
    _parse_output,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


# Complete response captured in the ending probe: stop, 74 completion tokens.
RESPONSE = '''{
  "interaction_intent": "chat",
  "history_query": "",
  "public_destination_quote": "",
  "ending_reason": "",
  "scene_complete": false,
  "transition_intent": "unclear",
  "natural_ending_ready": false,
  "metric_changes": {}
}'''


@pytest.fixture
def engine():
    return NumericV2Engine.from_mapping(numeric_v2_story())


@pytest.mark.parametrize('language', ['json', '', 'JSON'])
def test_complete_fence_preserves_evaluation(engine, language):
    plain = _parse_output(RESPONSE, engine, '谢谢，修得真不错。')
    fenced = _parse_output(f' \n```{language}\n{RESPONSE}\n```\n ', engine, '谢谢，修得真不错。')
    assert fenced == plain
    assert not fenced.scene_complete and not fenced.natural_ending_ready
    assert fenced.transition_intent == 'unclear'


@pytest.mark.parametrize('content', [
    f'结果如下：\n```json\n{RESPONSE}\n```',
    f'```json\n{RESPONSE}\n```\n可以继续。',
    f'```json\n{RESPONSE}\n```\n```json\n{RESPONSE}\n```',
    f'```python\n{RESPONSE}\n```',
    f'```json\n{RESPONSE}',
    f'```json\n{RESPONSE[:-1]}\n```',
    '{"ending_reason":"尚未完成',
])
def test_fence_does_not_repair_or_extract_json(engine, content):
    with pytest.raises(NumericV2EvaluatorOutputError, match='evaluator_invalid_json'):
        _parse_output(content, engine, '谢谢。')


@pytest.mark.parametrize('field,value,error', [
    ('scene_complete', 'true', 'scene_complete_invalid'),
    ('natural_ending_ready', 1, 'natural_ending_invalid'),
    ('metric_changes', [], 'changes_invalid'),
    ('transition_intent', 'yes', 'transition_intent_invalid'),
    ('goal_progress', {}, 'fields_invalid'),
])
def test_fence_keeps_field_validation(engine, field, value, error):
    payload = {**json.loads(RESPONSE), field: value}
    with pytest.raises(NumericV2EvaluatorOutputError, match=error):
        _parse_output(f'```json\n{json.dumps(payload)}\n```', engine, '谢谢。')


def test_fence_does_not_authorize_unverified_destination(engine):
    payload = {**json.loads(RESPONSE), 'transition_intent': 'initiate',
               'public_destination_quote': '我们去未公开的房间。'}
    plain = _parse_output(json.dumps(payload), engine, '带路吧。')
    fenced = _parse_output(f'```json\n{json.dumps(payload)}\n```', engine, '带路吧。')
    assert fenced == plain
    assert fenced.transition_intent == 'unclear'
    assert fenced.public_destination_quote == ''


def test_stale_invitation_requires_reply_target_binding(engine):
    """邀请隔过一轮后，含糊同意必须明确指向原邀请，不能抢走最近互动的回复。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id="reply_target",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我们去阅览室，好吗？"},
    )
    session = replace(
        session,
        revision=2,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "我们去阅览室，好吗？",
                "suggested_inputs": ["好，带路吧，我们去阅览室。"],
                "transition_offered": True,
            },
            {
                "revision": 2,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "你帮我看着门口，好吗？",
                "transition_offered": True,
            },
        ),
    )
    payload = {
        **json.loads(RESPONSE),
        "transition_intent": "accept",
        # 即使模型声称指向旧邀请，确定性校验仍须找到旧邀请独有的逐字证据。
        "transition_reply_target": "pending_transition",
    }
    ambiguous = _parse_output(json.dumps(payload), engine, "放心交给我，你去吧。", session)
    explicit = _parse_output(json.dumps(payload), engine, "好，我们现在去阅览室。", session)
    selected = _parse_output(json.dumps(payload), engine, "带路吧。", session)
    payload.update({
        "transition_intent": "initiate",
        "transition_reply_target": "other",
        "public_destination_quote": "我们去阅览室，好吗？",
    })
    bypass = _parse_output(json.dumps(payload), engine, "听你的。", session)

    assert ambiguous.transition_intent == "unclear"
    assert ambiguous.transition_reply_target == "pending_transition"
    assert explicit.transition_intent == "accept"
    assert selected.transition_intent == "accept"
    assert bypass.transition_intent == "unclear"


def test_represented_invitation_refreshes_reply_binding(engine):
    """正文在上一轮重新公开有效邀请时，玩家可用自然的“带路吧”直接接受。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id="refreshed_reply_target",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "开场。"},
    )
    session = replace(
        session,
        revision=2,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "我们去阅览室，好吗？",
                "transition_offered": True,
                "transition_offer_presented": True,
            },
            {
                "revision": 2,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "去阅览室呀，跟我来吧。",
                "transition_offered": True,
                "transition_offer_presented": True,
            },
        ),
    )
    payload = {
        **json.loads(RESPONSE),
        "transition_intent": "accept",
        "transition_reply_target": "pending_transition",
    }

    result = _parse_output(json.dumps(payload), engine, "好，带路吧。", session)

    assert result.transition_intent == "accept"


def test_latest_visible_acceptance_button_can_bind_repeated_invitation(engine):
    """旧邀请被最新正文重述后，用户点击最新接受按钮仍应正常换幕。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id="repeated_offer_button",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "开场。"},
    )
    session = replace(
        session,
        revision=2,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "留下来吧，一起建个小屋，怎么样？",
                "suggested_inputs": ["接下来怎么办？"],
                "transition_offered": True,
                "transition_offer_presented": True,
            },
            {
                "revision": 2,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "在这里建个小屋一起生活，你觉得怎么样？",
                "suggested_inputs": ["好啊，我们就在这里安家吧。"],
                "transition_offered": True,
            },
        ),
    )
    payload = {
        **json.loads(RESPONSE),
        "transition_intent": "accept",
        "transition_reply_target": "pending_transition",
    }

    result = _parse_output(
        json.dumps(payload), engine, "好啊，我们就在这里安家吧。", session,
    )

    assert result.transition_intent == "accept"


def test_latest_button_does_not_bind_unrelated_stale_invitation(engine):
    """最新互动没有重述旧邀请时，其按钮不能误接受旧邀请。"""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id="unrelated_latest_button",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "开场。"},
    )
    session = replace(
        session,
        revision=2,
        transition_offered=True,
        performance_history=(
            {
                "revision": 1,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "我们去阅览室，好吗？",
                "transition_offered": True,
                "transition_offer_presented": True,
            },
            {
                "revision": 2,
                "from_node_id": "start",
                "to_node_id": "start",
                "performance": "你帮我看着门口，好吗？",
                "suggested_inputs": ["好，我帮你看门。"],
                "transition_offered": True,
            },
        ),
    )
    payload = {
        **json.loads(RESPONSE),
        "transition_intent": "accept",
        "transition_reply_target": "pending_transition",
    }

    result = _parse_output(
        json.dumps(payload), engine, "好，我帮你看门。", session,
    )

    assert result.transition_intent == "unclear"


def test_evaluator_rejects_target_scene_fact_before_entering_target(engine):
    """出口预览不能让判定器提前完成尚未进入的目标幕。"""  # noqa: DOCSTRING_CJK

    engine.fact_contract["scene:next:life_established"] = {
        "value_type": "bool",
        "visibility": "public",
    }
    payload = json.loads(RESPONSE)
    payload["fact_candidates"] = [{
        "op": "set",
        "key": "scene:next:life_established",
        "value": True,
        "visibility": "public",
        "confidence": "confirmed",
        "subject": "玩家",
        "action": "同意",
        "object": "定居",
        "result": "开始共同生活",
        "evidence": [{"source": "player_input", "quote": "好，我们留下来。"}],
    }]
    session = engine.create_session(
        session_id="target_fact_scope",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "开场。"},
    )

    result = _parse_output(
        json.dumps(payload, ensure_ascii=False),
        engine,
        "好，我们留下来。",
        session,
    )

    assert result.fact_operations == ()


def test_evaluator_accepts_only_evidenced_fact_candidates():
    """判定器只把剧本合同内、能从玩家原话逐字核对的候选交给 Runtime。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["fact_contract"] = {
        "facts": {
            "prop:old_letter": {"value_type": "string", "visibility": "public"},
        }
    }
    candidate = {
        "op": "set",
        "key": "prop:old_letter",
        "value": "柜台抽屉里的旧信",
        "visibility": "public",
        "confidence": "confirmed",
        "subject": "环境",
        "action": "放置",
        "object": "旧信",
        "result": "旧信位于柜台抽屉",
        "evidence": [{"source": "player_input", "quote": "我看到柜台抽屉里的旧信。"}],
    }
    payload = json.loads(RESPONSE)
    payload["fact_candidates"] = [candidate]
    result = _parse_output(
        json.dumps(payload, ensure_ascii=False),
        NumericV2Engine.from_mapping(story),
        "我看到柜台抽屉里的旧信。",
    )

    assert result.fact_operations == ({
        "op": "set",
        "key": "prop:old_letter",
        "value": "柜台抽屉里的旧信",
        "visibility": "public",
    },)
    assert result.fact_audit[0]["result"] == "旧信位于柜台抽屉"


def test_evaluator_drops_fact_candidates_without_contract_or_verifiable_quote(engine):
    """脏事实候选不能阻断合法回合，也不能绕过剧本白名单。"""  # noqa: DOCSTRING_CJK

    payload = json.loads(RESPONSE)
    payload["fact_candidates"] = [{
        "op": "set",
        "key": "prop:unknown",
        "value": "未知",
        "visibility": "public",
        "confidence": "confirmed",
        "subject": "环境",
        "action": "出现",
        "object": "未知",
        "result": "未知已出现",
        "evidence": [{"source": "player_input", "quote": "不在输入里的话"}],
    }]
    result = _parse_output(json.dumps(payload, ensure_ascii=False), engine, "谢谢。")

    assert result.fact_operations == ()
    assert result.fact_audit == ()

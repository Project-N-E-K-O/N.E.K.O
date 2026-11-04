"""Evaluator accepts a complete JSON fence without repairing ambiguous output."""

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.theater import numeric_v2_evaluator as evaluator
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
  "exit_matches": true,
  "exit_comparison": "玩家要求与出口安排一致。",
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


@pytest.mark.parametrize("matches,comparison,intent", [
    (False, "玩家去避风棚，出口前往交付院落。", "unclear"),
    (True, "", "unclear"),
    (True, "玩家与出口都是前往交付院落。", "initiate"),
])
def test_initiation_requires_exit_alignment_and_public_quote(engine, matches, comparison, intent):
    session = engine.create_session(
        session_id="exit_alignment", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "前面就是交付院落，我们可以过去。"},
    )
    payload = {**json.loads(RESPONSE), "transition_intent": "initiate",
               "exit_comparison": comparison, "exit_matches": matches,
               "public_destination_quote": "前面就是交付院落，我们可以过去。"}
    result = _parse_output(json.dumps(payload), engine, "带路吧。", session)
    assert result.transition_intent == intent
    for field in ("exit_matches", "exit_comparison"):
        incomplete = {key: value for key, value in payload.items() if key != field}
        assert _parse_output(json.dumps(incomplete), engine, "带路吧。", session).transition_intent == "unclear"
    payload["public_destination_quote"] = "尚未公开的另一处地点。"
    assert _parse_output(json.dumps(payload), engine, "带路吧。", session).transition_intent == "unclear"


@pytest.mark.parametrize("field,value", [("exit_matches", "false"), ("exit_matches", 1), ("exit_comparison", [])])
def test_exit_alignment_rejects_wrong_types(engine, field, value):
    payload = {**json.loads(RESPONSE), field: value}
    with pytest.raises(NumericV2EvaluatorOutputError, match="exit_alignment_invalid"):
        _parse_output(json.dumps(payload), engine, "先去避风棚。")


@pytest.mark.parametrize('active', [False, True])
def test_stale_invitation_requires_reply_target_binding(engine, active):
    """After an intervening turn, ambiguous assent must explicitly reference the original invitation, not steal the latest interaction's reply."""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id="reply_target",
        catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我们去阅览室，好吗？"},
    )
    session = replace(
        session,
        revision=2,
        transition_offered=active,
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
                "transition_offered": active,
            },
        ),
    )
    payload = {
        **json.loads(RESPONSE),
        "transition_intent": "accept",
        "exit_matches": True, "exit_comparison": "原邀请与当前出口的行动一致。",
        # Even if the model claims to reference an old invitation, deterministic validation must find verbatim evidence unique to it.
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


@pytest.mark.parametrize('destination', ['长街寻找旧信', '轨道站核对星图'])
@pytest.mark.parametrize('boundary', ['withdrawn', 'invalidated', 'revisited', 'missing'])
def test_withdrawn_acceptance_requires_a_current_valid_origin(engine, destination, boundary):
    """Explicit renewed acceptance may preserve the model decision, but cannot cross wrong-invitation or scene-visit boundaries."""  # noqa: DOCSTRING_CJK

    session = engine.create_session(
        session_id='withdrawn_parser', catgirl_binding={'catgirl_name': '测试猫娘'},
        opening_performance={'performance': '先聊聊吧。'},
    )
    offer = {
        'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
        'transition_offered': True, 'transition_offer_presented': True,
        'performance': f'要和我一起去{destination}吗？',
        'suggested_inputs': [f'好，我们去{destination}。'],
    }
    withdrawn = {
        'revision': 2, 'from_node_id': 'start', 'to_node_id': 'start',
        'transition_offered': False, 'input_text': '先不去，等等。', 'performance': '好，先留在这里。',
    }
    if boundary == 'invalidated':
        withdrawn['transition_offer_invalidated'] = True
    elif boundary == 'revisited':
        withdrawn['from_node_id'] = 'elsewhere'
    session = replace(session, revision=2, transition_offered=False,
                      performance_history=() if boundary == 'missing' else (offer, withdrawn))
    payload = {**json.loads(RESPONSE), 'transition_intent': 'accept',
               'transition_reply_target': 'pending_transition'}
    parsed = _parse_output(json.dumps(payload), engine, f'我改主意了，我们去{destination}。', session)
    assert parsed.transition_intent == ('accept' if boundary == 'withdrawn' else 'unclear')
    # Without a new model acceptance decision, a destination or historical invitation alone cannot restore authorization.
    payload['transition_intent'] = 'unclear'
    assert _parse_output(json.dumps(payload), engine, f'去{destination}的事再说吧。', session).transition_intent == 'unclear'


def test_represented_invitation_refreshes_reply_binding(engine):
    """When the previous turn publicly restates a valid invitation, the player can accept naturally with "带路吧"."""  # noqa: DOCSTRING_CJK

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
        "exit_matches": True, "exit_comparison": "原邀请与当前出口的行动一致。",
        "transition_reply_target": "pending_transition",
    }

    result = _parse_output(json.dumps(payload), engine, "好，带路吧。", session)

    assert result.transition_intent == "accept"


def test_latest_visible_acceptance_button_can_bind_repeated_invitation(engine):
    """After the latest body restates an old invitation, clicking its latest accept button must still switch scenes normally."""  # noqa: DOCSTRING_CJK

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
        "exit_matches": True, "exit_comparison": "原邀请与当前出口的行动一致。",
        "transition_reply_target": "pending_transition",
    }

    result = _parse_output(
        json.dumps(payload), engine, "好啊，我们就在这里安家吧。", session,
    )

    assert result.transition_intent == "accept"


def test_latest_button_does_not_bind_unrelated_stale_invitation(engine):
    """Without restating the old invitation in the latest interaction, its button must not accidentally accept that invitation."""  # noqa: DOCSTRING_CJK

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
    """Exit previews must not let the judge prematurely finish a destination scene not yet entered."""  # noqa: DOCSTRING_CJK

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
    """The judge passes Runtime only candidates within the script contract, verifiable verbatim against the player's original words."""  # noqa: DOCSTRING_CJK

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
    """Dirty fact candidates must neither block valid turns nor bypass the script allowlist."""  # noqa: DOCSTRING_CJK

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


def _offer_acceptance_payload(**changes):
    return {
        "offer_present": True, "offer_quote": "我们去阅览室，好吗？", "offer_kind": "invitation",
        "valid": True, "body_violations": [], "offer_acceptance_valid": True,
        "suggestion_checks": [{"index": 0, "decision": "allow", "requires": []}],
        **changes,
    }


@pytest.mark.parametrize("repetitions", [8, 16, 32])
def test_offer_quote_keeps_complete_current_invitation_beyond_diagnostic_budget(repetitions):
    """Preserve complete invitation quotes independently of the short Actor diagnostic budget."""
    quote = "走廊的灯已经亮起，通往阅览室的门也已经打开。" * repetitions + "我们一起过去，好吗？"
    assert evaluator.count_tokens(quote) > evaluator.NUMERIC_V2_TRANSITION_FAILURE_REASON_MAX_TOKENS
    result = evaluator._parse_transition_judge_output(
        json.dumps(_offer_acceptance_payload(offer_quote=quote), ensure_ascii=False),
        require_offer_acceptance=True, offer_evidence_text=quote,
        display_suggestions=("好，现在去阅览室。",),
    )
    assert result.offer_present and result.valid and result.offer_acceptance_valid
    assert result.offer_quote == quote


@pytest.mark.parametrize("current_source", [True, False])
def test_long_offer_quote_preserves_source_checks_and_independent_body_veto(current_source):
    """Removing hidden quote limits must not authorize stale quotes or invalid bodies."""
    quote = "走廊的灯已经亮起，通往阅览室的门也已经打开。" * 8 + "我们一起过去，好吗？"
    result = evaluator._parse_transition_judge_output(
        json.dumps(_offer_acceptance_payload(
            offer_quote=quote, body_violations=["author_boundary"],
            failure_reason="候选违反了作者明确规定的当前时点。" * 16,
        ), ensure_ascii=False),
        require_offer_acceptance=True,
        offer_evidence_text=quote if current_source else "现在仍留在柜台，暂时不前往阅览室。",
        display_suggestions=("好，现在去阅览室。",),
    )
    assert result.offer_present is current_source
    assert result.valid is current_source
    assert result.offer_quote == (quote if current_source else "")
    assert result.body_violations == ("author_boundary",)
    assert not result.offer_acceptance_valid
    assert evaluator.count_tokens(result.failure_reason) <= evaluator.NUMERIC_V2_TRANSITION_FAILURE_REASON_MAX_TOKENS


@pytest.mark.parametrize("changes,choices,expected", [
    ({}, ("好，现在去阅览室。",), True),
    ({"offer_acceptance_valid": False}, ("先不去。",), False),
    ({"offer_acceptance_valid": False}, ("晚一点再去。",), False),
    ({"offer_acceptance_valid": False}, ("先问你个问题。",), False),
    ({"offer_acceptance_valid": False}, ("好，去楼顶。",), False),
    ({"offer_acceptance_valid": False}, ("去阅览室后我马上签约。",), False),
    ({"offer_present": False, "offer_quote": ""}, ("好，现在去阅览室。",), False),
    ({"offer_quote": "不存在于本稿的邀请"}, ("好，现在去阅览室。",), False),
    ({"offer_kind": "exit_mention_only"}, ("好，现在去阅览室。",), False),
    ({"valid": False}, ("好，现在去阅览室。",), False),
    ({"body_violations": ["player_action"]}, ("好，现在去阅览室。",), False),
    ({"suggestion_checks": [{"index": 0, "decision": "reject", "requires": []}]},
     ("好，现在去阅览室。",), False),
    ({"suggestion_checks": []}, ("好，现在去阅览室。",), False),
    ({"suggestion_checks": [], "unsafe_suggestion_indexes": []}, None, False),
    ({}, ("",), False),
    ({"offer_quote": ""}, ("好，现在去阅览室。",), False),
])
def test_offer_acceptance_requires_independent_acceptance_and_matching_safe_invitation(changes, choices, expected):
    """Valid buttons alone cannot authorize acceptance without independent checks and safe invitations."""
    payload = _offer_acceptance_payload(**changes)
    if choices is None:
        payload.pop("suggestion_checks")
    result = evaluator._parse_transition_judge_output(
        json.dumps(payload, ensure_ascii=False),
        require_offer_acceptance=True, offer_evidence_text="我们去阅览室，好吗？",
        display_suggestions=choices,
    )
    assert result.offer_acceptance_valid is expected


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_offer_acceptance_missing_or_mistyped_cannot_authorize(value):
    payload = _offer_acceptance_payload()
    if value is None:
        payload.pop("offer_acceptance_valid")
    else:
        payload["offer_acceptance_valid"] = value
    with pytest.raises(NumericV2EvaluatorOutputError, match="transition_judge_fields_invalid"):
        evaluator._parse_transition_judge_output(
            json.dumps(payload), require_offer_acceptance=True,
            offer_evidence_text="我们去阅览室，好吗？", display_suggestions=("好，现在去阅览室。",),
        )


def test_offer_acceptance_is_not_inferred_or_consumed_by_default():
    payload = _offer_acceptance_payload()
    payload.pop("offer_acceptance_valid")
    result = evaluator._parse_transition_judge_output(
        json.dumps(payload), display_suggestions=("好，现在去阅览室。",),
    )
    assert result.valid and result.approved_suggestion_texts == ("好，现在去阅览室。",)
    assert result.offer_acceptance_valid is False
    payload["offer_acceptance_valid"] = True
    with pytest.raises(NumericV2EvaluatorOutputError, match="transition_judge_fields_invalid"):
        evaluator._parse_transition_judge_output(json.dumps(payload), display_suggestions=("好，现在去阅览室。",))


@pytest.mark.parametrize("dependent", [False, True])
def test_offer_acceptance_requires_actual_evidence_and_independent_button(dependent):
    payload = _offer_acceptance_payload()
    if dependent:
        payload["suggestion_checks"][0] = {"index": 0, "decision": "after_display", "requires": ["0"]}
        payload["fixed_narration_triggers"] = []
    result = evaluator._parse_transition_judge_output(
        json.dumps(payload), require_offer_acceptance=True,
        offer_evidence_text="我们去阅览室，好吗？" if dependent else "",
        display_suggestions=("好，现在去阅览室。",),
        fixed_narration_ids=("note",), allow_after_display=dependent,
        fixed_narration_review=dependent,
    )
    assert result.offer_acceptance_valid is False


@pytest.mark.asyncio
@pytest.mark.parametrize("require_acceptance", [False, True])
@pytest.mark.parametrize("completion_only", [False, True])
async def test_offer_acceptance_uses_one_existing_review_request_with_opt_in_contract(
    engine, monkeypatch, require_acceptance, completion_only,
):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    session = engine.create_session(
        session_id="offer_acceptance", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我们还在门厅。"},
    )
    payload = _offer_acceptance_payload()
    if not require_acceptance:
        payload.pop("offer_acceptance_valid")
    monkeypatch.setattr(evaluator, "_model_config", AsyncMock(return_value={
        "model": "offline", "base_url": "https://example.invalid", "api_key": "test",
    }))
    factory = AsyncMock(return_value=Client())
    invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload)))
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "invoke_with_usage", invoke)
    monkeypatch.setattr(evaluator, "response_format_for", lambda _config, _name, schema: schema)
    if completion_only and require_acceptance:
        monkeypatch.setattr(evaluator, "_pending_completion_facts", lambda *_args: [
            {"key": "scene:start:pending_check", "value": True, "description": "检查实际完成"},
        ])
    kwargs = {"require_offer_acceptance": True} if require_acceptance else {}
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message="",
        actor_performance={"performance": "我们去阅览室，好吗？", "suggested_inputs": ["好，现在去阅览室。"]},
        completion_fact_review_only=completion_only, **kwargs,
    )
    assert factory.await_count == invoke.await_count == 1
    assert factory.call_args.kwargs["max_retries"] == 0
    system, human = invoke.call_args.args[1]
    assert "我们去阅览室，好吗？" in human.content
    schema = invoke.call_args.kwargs["response_format"]
    assert ("offer_acceptance_valid" in system.content) is require_acceptance
    assert ("offer_acceptance_valid" in schema["properties"]) is require_acceptance
    assert ("offer_acceptance_valid" in schema["required"]) is require_acceptance
    if require_acceptance:
        assert schema["properties"]["offer_acceptance_valid"] == {"type": "boolean"}
        assert all(text in system.content for text in ("第一", "无条件", "拒绝", "暂缓", "旁支", "重要操作"))
        if completion_only:
            # 真正发送的纯邀请任务不能仍让模型核验不存在的新完成结果。
            assert "待展示邀请" in system.content
            assert "同一场所" in system.content and "status=eligible" in system.content
            assert "新完成结果的独立核验者" not in system.content
            assert "fact_candidates" not in schema["properties"]
            assert "pending_completion_facts" not in json.loads(human.content[human.content.index("{"):])
    else:
        expected_messages, _ = evaluator._build_transition_judge_messages(
            engine, session, player_input="",
            actor_performance={"performance": "我们去阅览室，好吗？", "suggested_inputs": ["好，现在去阅览室。"]},
            completion_fact_review_only=completion_only, require_offer_acceptance=False,
        )
        assert [item.content for item in (system, human)] == [item.content for item in expected_messages]
        assert schema == evaluator.review_output_schema(
            display_suggestions=True, completion_fact_review_only=completion_only,
        )
    assert result.offer_acceptance_valid is require_acceptance


# Evaluator 输出预算与截断响应保持原有事实、授权拒绝边界。
def _context(current_count=0, global_count=0, future_count=0):
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {
        **{f"scene:start:fact_{index}": {"value_type": "bool", "visibility": "public"}
           for index in range(current_count)},
        **{f"prop:fact_{index}": {"value_type": "bool", "visibility": "public"}
           for index in range(global_count)},
        **{f"scene:next:fact_{index}": {"value_type": "bool", "visibility": "public"}
           for index in range(future_count)},
    }}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="output_budget", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我在这里。"},
    )
    return engine, session


async def _call(monkeypatch, engine, session, content=None, finish_reason="stop"):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(evaluator, "_model_config", AsyncMock(return_value={
        "model": "offline", "base_url": "https://example.invalid", "api_key": "test",
    }))
    factory = AsyncMock(return_value=Client())
    invoke = AsyncMock(return_value=SimpleNamespace(
        content=content if content is not None else json.dumps({
            "scene_complete": False, "metric_changes": {}, "fact_candidates": [],
        }), response_metadata={"finish_reason": finish_reason},
    ))
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(evaluator, "invoke_with_usage", invoke)
    result = await evaluator.NumericV2MetricEvaluator(object()).evaluate(
        engine=engine, session=session, message="我选官道，现在就出发，先去官道旁的驿站。",
    )
    assert invoke.await_count == factory.await_count == 1
    assert factory.call_args.kwargs["max_retries"] == 0
    assert factory.call_args.kwargs["timeout"] == 12.0
    return result, factory.call_args.kwargs["max_completion_tokens"]


@pytest.mark.asyncio
@pytest.mark.parametrize("current_count,global_count,future_count,expected", [
    (0, 0, 0, 360), (0, 0, 30, 360), (1, 0, 0, 488), (0, 2, 0, 616),
    (1, 1, 30, 616), (16, 0, 0, 2408), (17, 0, 0, 2408), (4, 12, 30, 2408),
])
async def test_only_current_and_global_writable_facts_expand_output_budget(
    monkeypatch, current_count, global_count, future_count, expected,
):
    engine, session = _context(current_count, global_count, future_count)
    result, budget = await _call(monkeypatch, engine, session)

    assert budget == expected
    assert result.fact_operations == ()


@pytest.mark.asyncio
async def test_original_two_fact_length_response_remains_all_uncommitted(monkeypatch):
    story = numeric_v2_story()
    story["start_node_id"] = story["nodes"][0]["id"] = "mainline_01"
    story["fact_contract"] = {"facts": {
        "scene:mainline_01:route_decided": {"value_type": "string", "visibility": "public"},
        "scene:mainline_01:departure_confirmed": {"value_type": "bool", "visibility": "public"},
    }}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="captured_length", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "官道与山径都可以选择。"},
    )
    result, budget = await _call(
        monkeypatch, engine, session, content=CAPTURED_TRUNCATED_RESPONSE, finish_reason="length",
    )

    assert budget == 616
    assert budget > 360  # The provider reported exactly 360 completion tokens for this unfinished original.
    assert result.scene_complete is True
    assert result.fact_operations == result.fact_audit == ()
    assert result.transition_intent == "unclear"


# Original Qwen response from the official-road regression, ending midway through the second evidence object.
# Embedded verbatim so the regression does not depend on the local experiment directory.
CAPTURED_TRUNCATED_RESPONSE = '{\n  "public_destination_quote": "",\n  "exit_comparison": "公开去向仅为官道/山径二选一，玩家要求去驿站并出发，transition_preview为blocked且无出口安排，三者不一致。",\n  "exit_matches": false,\n  "scene_complete": true,\n  "transition_intent": "unclear",\n  "transition_reply_target": "unclear",\n  "metric_changes": {\n    "escort_style": {\n      "strength": "normal",\n      "criterion_id": "escort_style.increase.1"\n    }\n  },\n  "fact_candidates": [\n    {\n      "op": "set",\n      "key": "scene:mainline_01:route_decided",\n      "value": "官道",\n      "visibility": "public",\n      "confidence": "confirmed",\n      "subject": "老谢",\n      "action": "选择",\n      "object": "路线",\n      "result": "官道",\n      "evidence": [\n        {\n          "source": "player_input",\n          "quote": "我选官道"\n        }\n      ]\n    },\n    {\n      "op": "set",\n      "key": "scene:mainline_01:departure_confirmed",\n      "value": true,\n      "visibility": "public",\n      "confidence": "confirmed",\n      "subject": "老谢",\n      "action": "同意",\n      "object": "启程",\n      "result": "确认",\n      "evidence": [\n        {\n          "source'


def test_new_actor_facts_keep_independent_causal_review(engine, monkeypatch):
    session = engine.create_session(session_id="causal-task", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": "先检查眼前的物件。"})
    pending = [{"key": "scene:start:checked", "value": True, "description": "检查已实际完成"}]
    monkeypatch.setattr(evaluator, "_pending_completion_facts", lambda *_args: pending)
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, player_input="请先检查。", completion_fact_review_only=True,
        require_offer_acceptance=True,
        actor_performance={"performance": "检查好了，接着去门边看看？", "suggested_inputs": ["好，去门边。"]},
        actor_fact_candidates=({"key": pending[0]["key"], "value": True, "evidence_quote": "检查好了"},),
    )
    assert "新完成结果的独立核验者" in messages[0].content
    assert "先独立从历史和本轮输入" in messages[0].content
    assert "approved_actor_fact_indexes" in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [
    {'completion_fact_review_only': False}, {'message': '好。'}, {'require_offer_acceptance': True},
    {'actor_performance': {'performance': '去门边吗？', 'suggested_inputs': ['好。']}},
    {'actor_performance': {'performance': '去门边吗？', 'fact_candidates': [{'key': 'checked'}]}},
    {'actor_performance': {'performance': '去门边吗？', 'scene_narration': '门打开了。'}},
    {'actor_performance': {'performance': '去门边吗？', 'scene_update': '门打开了。'}},
    {'evaluator_fact_claims': ({'key': 'checked'},)}, {'dispute_review': True}, {'route_changed': True},
    {'transition_outcome': object()}, {'check_missed_initiation': True}, {'confirmed_acceptance': True},
    {'confirmed_silent_acceptance': '好。'}, {'public_destination_quote': '去门边'},
    {'cancelled_transition': True}, {'invalidated_invitation': True},
])
async def test_invitation_proposal_review_rejects_other_authorities_before_config(engine, monkeypatch, invalid):
    session = engine.create_session(session_id='proposal_invalid', catgirl_binding={'catgirl_name': '测试猫娘'},
                                    opening_performance={'performance': '我们在门厅。'})
    config = AsyncMock(side_effect=AssertionError('must reject before model config'))
    monkeypatch.setattr(evaluator, '_model_config', config)
    kwargs = dict(engine=engine, session=session, message='', completion_fact_review_only=True,
                  invitation_proposal_only=True, actor_performance={'performance': '去门边吗？', 'suggested_inputs': []})
    kwargs.update(invalid)
    with pytest.raises(evaluator.NumericV2EvaluatorError, match='invitation_proposal_review_mode_invalid'):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**kwargs)
    config.assert_not_awaited()


@pytest.mark.asyncio
async def test_invitation_proposal_review_has_no_button_or_fact_authority(engine, monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    session = engine.create_session(session_id='proposal_only', catgirl_binding={'catgirl_name': '测试猫娘'},
                                    opening_performance={'performance': '我们在门厅。'})
    monkeypatch.setattr(evaluator, '_pending_completion_facts', lambda *_args: [
        {'key': 'scene:start:checked', 'value': True, 'description': '检查已经完成'},
    ])
    payload = _offer_acceptance_payload()
    payload.pop('offer_acceptance_valid')
    payload.pop('suggestion_checks', None)
    payload['unsafe_suggestion_indexes'] = []
    monkeypatch.setattr(evaluator, '_model_config', AsyncMock(return_value={
        'model': 'offline', 'base_url': 'https://example.invalid', 'api_key': 'test',
    }))
    monkeypatch.setattr(evaluator, 'create_chat_llm_async', AsyncMock(return_value=Client()))
    invoke = AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload)))
    monkeypatch.setattr(evaluator, 'invoke_with_usage', invoke)
    monkeypatch.setattr(evaluator, 'response_format_for', lambda _config, _name, schema: schema)
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message='', completion_fact_review_only=True,
        invitation_proposal_only=True,
        actor_performance={'performance': '我们去阅览室，好吗？', 'suggested_inputs': []},
    )
    assert invoke.await_count == 1
    system, human = invoke.call_args.args[1]
    data = json.loads(human.content[human.content.index('{'):])
    schema = invoke.call_args.kwargs['response_format']
    assert '待展示邀请' in system.content
    assert not any(key in data for key in ('pending_completion_facts', 'actor_fact_candidates', 'evaluator_fact_claims', 'suggested_inputs'))
    assert not any(key in schema['properties'] for key in (
        'suggestion_checks', 'offer_acceptance_valid', 'fact_candidates', 'approved_actor_fact_indexes',
    ))
    assert '第一按钮' not in system.content
    assert result.offer_present and result.valid
    assert not result.offer_acceptance_valid

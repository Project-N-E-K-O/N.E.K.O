"""Quiet player actions remain sendable choices without inventing dialogue."""

import json

import pytest

from services.theater import numeric_v2_actor as actor_module
from services.theater import numeric_v2_context as context_module
from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_actor_output import (
    NumericV2ActorOutputError, _parse_actor_suggestions, _parse_output,
)
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


@pytest.mark.parametrize('acceptance', [None, '', '（）', 42])
def test_invalid_acceptance_fill_cannot_promote_a_rejection(acceptance):
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({'accept_input': acceptance, 'alternative_inputs': ['（摇头）再等等。']}),
                      transition_suggestions_only=True)


@pytest.mark.parametrize('mode', ['ordinary', 'opening', 'formal', 'fill', 'acceptance_fill'])
def test_action_choices_keep_public_string_contract_in_every_actor_mode(mode):
    choices = ['（打开课本开始复习）', '（查看终端）请解释这条读数。', '你愿意解释一下吗？']
    payload = {'performance': '（点头）你先看看。', 'suggested_inputs': choices}
    options = {}
    if mode == 'opening':
        payload['scene_narration'] = '桌上摆着资料。'
        options['opening_required'] = True
    elif mode == 'formal':
        payload = {**_candidate(), 'suggested_inputs': choices}
        options['transition_required'] = True
    elif mode == 'fill':
        payload = {'suggested_inputs': choices}
        options['suggestions_only'] = True
    elif mode == 'acceptance_fill':
        choices = ['（点头同意现在出发）', '（摆手示意暂时留下）']
        payload = {'accept_input': choices[0], 'alternative_inputs': choices[1:]}
        options['transition_suggestions_only'] = True
    assert _parse_output(json.dumps(payload), **options)['suggested_inputs'] == choices


@pytest.mark.parametrize('invalid', [
    '', '（）', '（  ）', '（观察', '观察）', '玩家对白',
    '（观察（终端））', '（观察）明白了。（开始操作）',
    '（她递来课本）', '（环境安静下来）', '（查看[物品]）', '（' + '看' * 121 + '）',
])
def test_action_choices_do_not_relax_other_shape_and_owner_guards(invalid):
    valid = '（打开课本）'
    assert _parse_actor_suggestions([invalid, valid]) == [valid]


def test_action_choices_are_deduplicated_and_still_capped_at_three():
    choices = ['（打开课本）', '（查看终端）', '（喝一口水）', '（望向窗外）']
    assert _parse_actor_suggestions(['  （打开课本）  ', choices[0], choices[1]]) == choices[:2]
    assert _parse_actor_suggestions(choices) == choices[:3]


def test_action_choices_do_not_relax_character_performance_dialogue_policy():
    with pytest.raises(NumericV2ActorOutputError):
        _parse_output(json.dumps({
            'performance': '（点头）', 'suggested_inputs': ['（打开课本）'],
        }))


@pytest.mark.asyncio
async def test_real_quiet_study_output_keeps_two_choices_without_fill(monkeypatch):
    # Frozen Actor response from the wheat baseline, revision 1.
    choices = ['（打开书包，拿出课本和笔开始复习）', '（拿起桌上的温水喝了一口，然后翻开书本）']
    parsed = _parse_output(json.dumps({
        'performance': '（小葵转身回到柜台，继续整理剩下的面包）不客气，安静待着吧。',
        'suggested_inputs': choices, 'transition_offered': False,
    }))
    actor = NumericV2Actor(object())

    async def unexpected_call(*args, **kwargs):
        pytest.fail('Valid quiet actions must not need a supplemental model request')

    monkeypatch.setattr(actor, '_invoke', unexpected_call)
    assert await actor._ensure_suggestions(
        performance=parsed, player_input='谢谢。', catgirl_name='小葵', max_input_tokens=900,
    ) == choices
    assert actor.suggestion_fill_attempt_count == 0


# 纯对白与纯动作推荐共用原有格式校验，不为问话补造动作。
@pytest.mark.parametrize("text", [
    "你愿意多说一点吗？",
    "您刚才说的是什么意思？",
    "他为什么会这样说？",
    "我想先听听。",
    "（留在原地）",
    "（点头）好，我听着。",
    "(点头)好，我听着。",
    "这段对白是什么意思？",
    "这段对白：你觉得哪里不自然？",
    "“对白”是什么意思？",
    "【对白录音】在哪里？",
    "**对白记录**是谁写的？",
    "（查看玩家动作记录）这段对白：你觉得哪里不自然？",
    "（查看玩家动作记录）这是谁保存的？",
])
def test_supported_player_input_shapes_preserve_text(text):
    assert _parse_actor_suggestions([text]) == [text]


@pytest.mark.parametrize("label", ["玩家动作", "玩家对白", "动作", "对白"])
@pytest.mark.parametrize("template", ["{}", "（{}）好。", "（点头）{}"])
def test_format_labels_never_become_sendable_inputs(label, template):
    diagnostics = {}
    assert _parse_actor_suggestions(
        [template.format(label), "我先听听。"], diagnostics=diagnostics,
    ) == ["我先听听。"]
    assert diagnostics["placeholder_item"] == 1


@pytest.mark.parametrize("label", ["玩家动作", "玩家对白", "动作", "对白"])
@pytest.mark.parametrize("wrapper", [
    "{}：我先听听。",
    "{} \t: 我先听听。",
    "【{}】我先听听。",
    "**{}**：我先听听。",
    "“{}”：我先听听。",
    '"{}" : 我先听听。',
])
def test_wrapped_format_labels_are_dropped_without_rewriting_body(label, wrapper):
    invalid = wrapper.format(label)
    diagnostics = {}
    parsed = _parse_output(json.dumps({
        "performance": "有想问的就说。",
        "suggested_inputs": [invalid, "你愿意多说一点吗？"],
        "transition_offered": False,
    }), suggestion_diagnostics=diagnostics)
    assert parsed["performance"] == "有想问的就说。"
    assert parsed["suggested_inputs"] == ["你愿意多说一点吗？"]
    assert diagnostics["placeholder_item"] == 1


@pytest.mark.parametrize("text", [
    "（点头）玩家对白：我先听听。",
    "（动作：点头）我先听听。",
    "（点头）【对白】我先听听。",
    "（点头）**对白**：我先听听。",
    "（点头）“对白”：我先听听。",
])
def test_wrapped_format_labels_are_checked_at_each_block_start(text):
    assert _parse_actor_suggestions([text, "（留在原地）"]) == ["（留在原地）"]


@pytest.mark.parametrize("invalid", [None, False, True, 1, 1.5, {}, {"text": "好。"}, ["好。"]])
def test_nonstring_items_are_not_coerced_into_dialogue(invalid):
    parsed = _parse_output(json.dumps({
        "performance": "你可以先看看。",
        "suggested_inputs": [invalid, "我先听听。"],
        "transition_offered": False,
    }))
    assert parsed["performance"] == "你可以先看看。"
    assert parsed["suggested_inputs"] == ["我先听听。"]


@pytest.mark.parametrize("invalid", [
    "好。（点头）",
    "（点头）（微笑）",
    "（点头）好。（继续阅读）",
    "（点头",
    "点头）好。",
    "（（点头））",
    "（你点头）好。",
    "（她点头）好。",
    "（环境变亮）",
    "[玩家对白]",
])
def test_invalid_mixed_shapes_and_nonplayer_actions_remain_rejected(invalid):
    assert _parse_actor_suggestions([invalid, "（留在原地）"]) == ["（留在原地）"]


def test_plain_dialogue_keeps_length_normalization_duplicate_and_count_limits():
    diagnostics = {}
    choices = ['  你说的"先等等"是什么意思？  ', '你说的“先等等”是什么意思？', "好" * 120]
    assert _parse_actor_suggestions(choices, diagnostics=diagnostics) == [
        '你说的“先等等”是什么意思？', "好" * 120,
    ]
    assert diagnostics["duplicate_item"] == 1
    assert _parse_actor_suggestions(["好" * 121], diagnostics=diagnostics) == []
    assert diagnostics["too_long_item"] == 1
    assert _parse_actor_suggestions(
        ["先听听。", "再想想。", "我先看看。", "暂时不说。"], diagnostics=diagnostics,
    ) == ["先听听。", "再想想。", "我先看看。"]
    assert diagnostics["too_many_items"] == 1


@pytest.mark.parametrize("mode", ["opening", "ordinary", "transition", "suggestions_only"])
def test_all_actor_suggestion_paths_accept_dialogue_without_changing_body(mode):
    suggestions = ["你愿意多说一点吗？", "（留在原地）", "（点头）好。"]
    options = {}
    if mode == "opening":
        body = {"scene_narration": "窗外有雨声。", "performance": "你想先聊什么？"}
        options["opening_required"] = True
    elif mode == "transition":
        body = {
            "source_performance": "那就走吧。",
            "bridge_scene_narration": "走廊通向露台。",
            "target_scene_narration": "露台上有风。",
            "target_performance": "这里视野很好。",
        }
        options["transition_required"] = True
    elif mode == "suggestions_only":
        body = {}
        options["suggestions_only"] = True
    else:
        body = {"performance": "你想先聊什么？"}
    parsed = _parse_output(json.dumps({**body, "suggested_inputs": suggestions}), **options)
    assert parsed["suggested_inputs"] == suggestions
    assert {key: parsed[key] for key in body} == body


def test_transition_fill_preserves_explicit_acceptance_first_and_alternatives():
    parsed = _parse_output(json.dumps({
        "accept_input": "好，我们去露台。",
        "alternative_inputs": ["先等一下。", "（留在原地）"],
    }), transition_suggestions_only=True)
    assert parsed["suggested_inputs"] == ["好，我们去露台。", "先等一下。", "（留在原地）"]
    with pytest.raises(NumericV2ActorOutputError, match="suggestions_invalid"):
        _parse_output(json.dumps({"accept_input": "好。", "alternative_inputs": []}),
                      transition_suggestions_only=True)


def test_player_suggestion_change_does_not_relax_character_dialogue_policy():
    with pytest.raises(NumericV2ActorOutputError, match="dialogue_required"):
        _parse_output(json.dumps({
            "performance": "（点头）",
            "suggested_inputs": ["你愿意多说一点吗？"],
        }), dialogue_policy="required")


@pytest.mark.parametrize("phase", ["opening", "turn", "transition_compact", "fill"])
def test_all_recommendation_generators_share_observation_intent_and_result_boundary(phase):
    # 同一规则必须覆盖主生成、开场、换幕及补推荐；格式解析不承担世界事实判断。
    rule = context_module.SUGGESTION_EVIDENCE_RULE
    if phase == "fill":
        prompt = actor_module._suggestion_fill_messages(
            catgirl_name="小葵", performance={"performance": "可以先看看。"},
            player_input="现在呢？", max_tokens=4000,
        )[0].content
    else:
        prompt = actor_module._system_prompt(
            catgirl_name="小葵", phase=phase,
        )
    if phase == "turn":
        assert "未知发现与成败留给正文" in prompt
        assert "涉及的外部事物、线索、他人已完成动作或结果须有可见正文或公开历史支持" in prompt
        assert "可提出待商量的偏好或新安排，不能写成既定事实" in prompt
    else:
        assert prompt.count(rule) == 1

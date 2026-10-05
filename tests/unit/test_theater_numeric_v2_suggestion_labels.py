"""A format label is not a player action, even in an otherwise valid response."""

import json

import pytest

from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_actor_output import _parse_actor_suggestions, _parse_output


@pytest.mark.parametrize('label', ['玩家动作', '玩家对白', '动作', '对白'])
def test_format_labels_do_not_become_sendable_actions(label):
    valid = '（查看终端）这条记录是什么意思？'
    diagnostics = {}
    assert _parse_actor_suggestions([f'（{label}）我先看一看。', valid], diagnostics=diagnostics) == [valid]
    assert diagnostics['placeholder_item'] == 1


def test_label_words_inside_real_action_or_dialogue_remain_valid():
    choices = ['（指向屏幕上的玩家动作记录）这是谁保存的？', '（翻开书页）这段对白是什么意思？']
    assert _parse_actor_suggestions(choices) == choices


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['信纸上的字迹清楚了。', '终端的记录已经显示。'])
async def test_bad_label_keeps_body_and_one_good_choice_without_refill(monkeypatch, text):
    choices = ['（玩家动作）我保持沉默。', '（留在原地阅读）']
    parsed = _parse_output(json.dumps({'performance': text, 'suggested_inputs': choices, 'transition_offered': False}))
    assert parsed['performance'] == text
    actor = NumericV2Actor(object())

    async def unexpected_call(*args, **kwargs):
        pytest.fail('One good ordinary choice must not cause another model call')

    monkeypatch.setattr(actor, '_invoke', unexpected_call)
    assert await actor._ensure_suggestions(performance=parsed, player_input='好。', catgirl_name='测试角色', max_input_tokens=1000) == ['（留在原地阅读）']
    assert actor.suggestion_fill_attempt_count == 0

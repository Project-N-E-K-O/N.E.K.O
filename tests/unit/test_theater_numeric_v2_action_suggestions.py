"""Quiet player actions remain sendable choices without inventing dialogue."""

import json

import pytest

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
    choices = ['（打开课本开始复习）', '（查看终端）请解释这条读数。']
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
    '', '（）', '（  ）', '（观察', '观察）', '我先观察一下。',
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

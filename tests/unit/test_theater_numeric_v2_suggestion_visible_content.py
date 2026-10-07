"""Supplemental choices see the same ordered fixed text that the player sees."""

import json

import pytest

from services.theater.numeric_v2_actor import _suggestion_fill_messages


@pytest.mark.parametrize('text', ['家书原文：不用替我守着旧日。', '航行记录：救援艇已返航，所有人员生还。'])
@pytest.mark.parametrize('position', ['before', 'after'])
@pytest.mark.parametrize('transition', [False, True])
def test_fixed_text_reaches_choices_without_private_metadata_or_old_scene(text, position, transition):
    visible = {'scene_narration': '桌面上显示文字。', 'performance': '你可以慢慢看。',
               'fixed_narrations': [{'id': 'private_piece_id', 'node_id': 'private_node_id',
                                     'position': position, 'text': text, 'bindings': {'private': 'hidden'}}]}
    performance = {'segments': [{'phase': 'source_response', 'performance': '旧幕已经结束。'},
                                {'phase': 'target_opening', **visible}]} if transition else visible
    messages = _suggestion_fill_messages(catgirl_name='测试角色', performance=performance,
        player_input='旧幕的输入。', max_tokens=10000, after_scene_change=transition)
    data = json.loads(messages[1].content)
    blocks = json.loads(data['visible_performance'])
    assert [b['text'] for b in blocks] == (
        ['桌面上显示文字。', text, '你可以慢慢看。'] if position == 'before'
        else ['桌面上显示文字。', '你可以慢慢看。', text])
    assert all(set(b) <= {'type', 'speaker_id', 'text'} for b in blocks)
    assert 'private_' not in data['visible_performance']
    assert '旧幕已经结束' not in data['visible_performance']
    if transition:
        assert data['player_input'] == ''

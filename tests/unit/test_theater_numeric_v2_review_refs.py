"""Keep narration identifiers local to a review request and restore authored IDs."""

from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluatorOutputError,
    _build_transition_judge_messages,
    _parse_transition_judge_output,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def test_review_uses_short_refs_for_pending_pieces_and_their_dependencies():
    ids = [str(i) * 128 for i in range(3)]
    story = numeric_v2_story()
    story['nodes'][0]['story_beat']['fixed_narrations'] = [
        {'id': key, 'text': '作者原文', 'trigger': {'type': 'condition', 'condition': '铭牌已登记。'},
         'after': ids[:index], 'required_before_exit': True}
        for index, key in enumerate(ids)
    ]
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='refs', catgirl_binding=_binding(),
                                    opening_performance={'performance': '请登记铭牌。', 'suggested_inputs': []})
    session = replace(session, performance_history=({'revision': 1, 'fixed_narrations': [
        {'node_id': 'start', 'id': ids[0], 'text': '作者原文', 'position': 'after'},
    ]},))
    messages, _ = _build_transition_judge_messages(
        engine, session, actor_performance={'performance': '登记完成。', 'suggested_inputs': []},
        player_input='登记铭牌。',
    )
    user = messages[1].content
    data = json.loads(user[user.index('{'):])
    candidates = data['fixed_narration_candidates']
    assert [item['id'] for item in candidates] == ['0', '1']
    assert [item['after'] for item in candidates] == [[], ['0']]
    assert all(key not in json.dumps(candidates) for key in ids)


@pytest.mark.parametrize('refs', [['1', '0'], ['2'], ['0', '0'], ['authored-id']])
def test_review_restores_only_unique_refs_from_the_sent_table(refs):
    authored_ids = ('authored-id', 'b' * 128)
    raw = json.dumps({'offer_present': False, 'valid': False, 'body_violations': [],
                      'unsafe_suggestion_indexes': [], 'fixed_narration_triggers': [
                          {'id': ref, 'evidence': '登记完成'} for ref in refs]})
    if refs == ['1', '0']:
        result = _parse_transition_judge_output(
            raw, fixed_narration_review=True, fixed_narration_ids=authored_ids)
        assert [item['id'] for item in result.fixed_narration_triggers] == list(reversed(authored_ids))
        assert all(item['evidence'] == '登记完成' for item in result.fixed_narration_triggers)
    else:
        with pytest.raises(NumericV2EvaluatorOutputError, match='fixed_narration_review_invalid'):
            _parse_transition_judge_output(
                raw, fixed_narration_review=True, fixed_narration_ids=authored_ids)

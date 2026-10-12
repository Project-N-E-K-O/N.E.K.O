"""Author speech envelopes: real SDK validators, no model or persisted Session."""
from copy import deepcopy

import pytest

from theater_workshop.sdk.generation.numeric_v2 import _validate_idea_outline, _validate_node_enhancement
from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError, NumericV2BranchService
from tests.unit.theater_workshop.test_numeric_v2_generation import _idea_outline
from tests.unit.theater_workshop.test_numeric_v2_branch import _path_result


GENRES = [
    ('science_fiction', '女主在完成回应后停止旧载体机能。'),
    ('fantasy', '女主入梦后暂时静默，听到玩家呼唤再回应。'),
    ('opaque', '女主完成事件甲。'),
]


def _case(policy, description):
    outline = deepcopy(_idea_outline())
    outline['character_state_arc']['stages'][0]['acting_contract']['dialogue_policy'] = policy
    goals = outline['mainline_chapters'][0]['ordered_goals']
    goals[0].update(owner='catgirl', delivery_type='catgirl_action', description=description,
                    evidence_mode='semantic', anchors=[], sources=['opening'], timing='turn',
                    dialogue_policy_after='forbidden')
    return outline


@pytest.mark.parametrize('genre,description', GENRES)
@pytest.mark.parametrize('policy,conflict', [('required', True), ('optional', False)])
@pytest.mark.parametrize('entry', ['mainline', 'enhancement', 'branch'])
def test_mixed_speech_stage_uses_node_wide_optional_envelope(genre, description, policy, conflict, entry):
    outline = _case(policy, description)
    original = deepcopy(outline)
    stage = outline['character_state_arc']['stages'][0]
    chapter = outline['mainline_chapters'][0]
    if entry == 'mainline':
        issues = _validate_idea_outline(outline, minimum=2, maximum=8)
        assert bool([i for i in issues if i['code'] == 'node_dialogue_policy_conflict']) is conflict
        assert not [i for i in issues if i['code'] != 'node_dialogue_policy_conflict']
    elif entry == 'enhancement':
        candidate = {k: deepcopy(chapter[k]) for k in ['opening_scene', 'narrative_focus', 'ordered_goals',
                     'catgirl_situation', 'transition_goal']}
        candidate['must_not_happen'] = []
        candidate['character_state'] = deepcopy(stage)
        issues = _validate_node_enhancement(candidate, node_type='start')
        assert bool([i for i in issues if i['code'] == 'node_dialogue_policy_conflict']) is conflict
        assert not [i for i in issues if i['code'] != 'node_dialogue_policy_conflict']
    else:
        plan = {'length': 1, 'continuity_items': [], 'condition_selection': {'mode': 'selected'},
                'endpoint_mode': 'mainline', 'context': {'global': {'intro': {}, 'key_props': []}}}
        candidate = _path_result(plan)
        candidate['scenes'][0]['ordered_goals'] = deepcopy(chapter['ordered_goals'])
        candidate['scenes'][0]['character_state'] = deepcopy(stage)
        candidate['scenes'][0]['character_state']['continuity_from_previous'] = ['上游已经发生事件乙。']
        if conflict:
            with pytest.raises(NumericV2BranchError) as error:
                NumericV2BranchService()._validate_path_result(plan, candidate)
            assert error.value.details['issue_code'] == 'node_dialogue_policy_conflict'
        else:
            result = NumericV2BranchService()._validate_path_result(plan, candidate)
            assert result['scenes'][0]['character_state']['acting_contract']['dialogue_policy'] == policy
            assert result['scenes'][0]['ordered_goals'][0]['dialogue_policy_after'] == 'forbidden'
    assert outline == original


@pytest.mark.parametrize('policy,after,delivery,conflict', [
    ('required', 'unchanged', 'catgirl_action', False),
    ('required', 'required', 'catgirl_dialogue', False),
    ('required', 'optional', 'catgirl_action', True),
    ('forbidden', 'forbidden', 'catgirl_action', False),
    ('forbidden', 'required', 'catgirl_action', True),
    ('forbidden', 'optional', 'catgirl_action', True),
    ('forbidden', 'unchanged', 'catgirl_dialogue', True),
    ('optional', 'required', 'catgirl_dialogue', False),
])
def test_explicit_policy_fields_not_plot_keywords(policy, after, delivery, conflict):
    outline = _case(policy, '女主睡眠、昏迷、停机、醒来，这只是作者文本。')
    goal = outline['mainline_chapters'][0]['ordered_goals'][0]
    goal['dialogue_policy_after'] = after
    goal['delivery_type'] = delivery
    issues = _validate_idea_outline(outline, minimum=2, maximum=8)
    assert bool([i for i in issues if i['code'] == 'node_dialogue_policy_conflict']) is conflict
    assert not [i for i in issues if i['code'] != 'node_dialogue_policy_conflict']

"""Author display references survive generation without becoming semantic facts."""

from copy import deepcopy
import json

import pytest

from theater_workshop.host import InProcessPackageGateway
from theater_workshop.sdk.generation.numeric_v2 import (
    NumericV2Generator, _validate_idea_outline, _continuation_output_contract,
)
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from .numeric_v2_fixture import numeric_v2_setup
from .test_numeric_v2_generation import _idea_outline, _generation_setup


def display_outline(genre='letter'):
    outline = _idea_outline()
    chapter = outline['mainline_chapters'][0]
    text = '家书：\n一切安好。' if genre == 'letter' else '[SYSTEM LOG]\n设备编号009，校验完成。'
    chapter['fixed_narrations'] = [{
        'id': 'original_text', 'text': text, 'after': [], 'required_before_exit': True,
        'trigger': {'type': 'condition', 'condition': '角色已实际触碰载体表面。', 'player_handoff_required': False},
    }]
    chapter['completion_facts'].append({
        'id': 'text_displayed', 'description': '原文已经作为旁白展示。',
        'value_type': 'bool', 'target_value': True, 'visibility': 'public',
        'fixed_narration_id': 'original_text',
    })
    chapter['exit_plan']['trigger_fact_ids'].append('text_displayed')
    return outline


def test_continuation_carries_display_and_trigger_shapes():
    chapter = _continuation_output_contract('mainline_chapters[1]')
    assert 'fixed_narration_id' in chapter['completion_facts'][0]
    assert chapter['fixed_narrations'][0]['trigger']['player_handoff_required'] is False


@pytest.mark.parametrize('genre', ['letter', 'science'])
def test_generation_projects_display_reference_without_creating_fact(genre):
    outline = display_outline(genre)
    before = deepcopy(outline)
    assert not _validate_idea_outline(outline, minimum=3, maximum=6, scene_expected_turns_target=8)
    story = NumericV2Generator()._project_story(title='验收', original_idea='验收',
        setup=numeric_v2_setup(), outline=outline, tone=['克制'])
    node = story['nodes'][0]
    assert node['completion_contract']['all'][-1] == {'fixed_narration_id': 'original_text'}
    assert 'scene:mainline_01:text_displayed' not in story['fact_contract']['facts']
    assert len(node['completion_contract']['all']) == len(outline['mainline_chapters'][0]['completion_facts'])
    assert node['story_beat']['fixed_narrations'] == outline['mainline_chapters'][0]['fixed_narrations']
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    assert compiled.story['nodes'][0]['completion_contract'] == node['completion_contract']
    assert outline == before


@pytest.mark.parametrize('fields', [
    {'fixed_narration_id': 'unknown'}, {'fixed_narration_id': []},
    {'value_type': 'string', 'target_value': 'true'}, {'target_value': False},
    {'target_value': 1}, {'visibility': 'story'},
])
def test_generation_rejects_invalid_display_reference(fields):
    outline = display_outline()
    outline['mainline_chapters'][0]['completion_facts'][-1].update(fields)
    issues = _validate_idea_outline(outline, minimum=3, maximum=6, scene_expected_turns_target=8)
    assert any(row['code'] == 'completion_display_reference_invalid' for row in issues)


def test_generation_rejects_duplicate_display_reference():
    outline = display_outline()
    facts = outline['mainline_chapters'][0]['completion_facts']
    facts.append({**facts[-1], 'id': 'duplicate_display'})
    issues = _validate_idea_outline(outline, minimum=3, maximum=6, scene_expected_turns_target=8)
    assert any(row['code'] == 'completion_display_reference_invalid' for row in issues)


def test_generation_keeps_unreferenced_semantic_fact_and_only_uses_exit_subset():
    outline = display_outline()
    outline['mainline_chapters'][0]['exit_plan']['trigger_fact_ids'] = ['text_displayed']
    story = NumericV2Generator()._project_story(title='验收', original_idea='验收',
        setup=numeric_v2_setup(), outline=outline, tone=['克制'])
    assert story['nodes'][0]['completion_contract'] == {'all': [{'fixed_narration_id': 'original_text'}]}
    assert 'scene:mainline_01:letter_date_verified' in story['fact_contract']['facts']


def test_generation_public_path_accepts_author_display_reference(monkeypatch):
    generator = NumericV2Generator()
    calls = []
    def model(messages, **kwargs):
        calls.append(kwargs['operation'])
        assert 'fixed_narration_id' in messages[0]['content']
        return json.dumps(display_outline(), ensure_ascii=False)
    monkeypatch.setattr(generator, 'call_llm', model)
    result = generator.generate(title='测试', setup=_generation_setup())
    assert result['story']['nodes'][0]['completion_contract']['all'][-1] == {'fixed_narration_id': 'original_text'}
    assert calls == ['numeric_v2_mainline_generation']

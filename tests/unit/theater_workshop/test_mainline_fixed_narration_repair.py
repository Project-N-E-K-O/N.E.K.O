"""Repair malformed author assets before projecting or repairing their references."""
from copy import deepcopy
import json

import pytest

from theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, _validate_idea_outline
from .test_display_completion_generation import display_outline
from .test_numeric_v2_generation import _generation_setup


@pytest.mark.parametrize('pieces', [
    ['s'], [{'id': 'original_text'}],
    [{'id': 'original_text', 'text': '原文', 'trigger': {'type': 'condition'},
      'after': [], 'required_before_exit': True}],
])
def test_bad_asset_requests_the_asset_array_not_just_the_fact_reference(pieces):
    candidate = display_outline()
    candidate['mainline_chapters'][0]['fixed_narrations'] = pieces
    issues = _validate_idea_outline(candidate, minimum=3, maximum=6)
    assert any(i['path'] == 'mainline_chapters[0].fixed_narrations' for i in issues)


def test_continuation_repairs_asset_and_preserves_reference_and_unrelated_prose():
    valid = display_outline()
    candidate = deepcopy(valid)
    candidate['mainline_chapters'][0]['fixed_narrations'] = ['s']
    calls = []
    def model(messages, **options):
        request = json.loads(messages[1]['content'])
        calls.append(request)
        replacements = {}
        for path in request['requested_paths']:
            if path.endswith('fixed_narrations'):
                replacements[path] = valid['mainline_chapters'][0]['fixed_narrations']
            elif '.completion_facts[' in path:
                replacements[path] = valid['mainline_chapters'][0]['completion_facts'][-1]
        return json.dumps({'replacements': replacements}, ensure_ascii=False)
    result = NumericV2Generator(model_call=model).generate(title='修复引用', setup=_generation_setup(),
        checkpoint={'candidate': candidate})
    assert len(calls) == 1
    assert result['story']['nodes'][0]['story_beat']['fixed_narrations'] == valid['mainline_chapters'][0]['fixed_narrations']
    assert result['story']['nodes'][1]['story_beat']['summary'] == candidate['mainline_chapters'][1]['narrative']
    assert candidate['mainline_chapters'][0]['fixed_narrations'] == ['s']


def test_condition_piece_in_terminal_ending_is_reported_before_projection():
    candidate = display_outline()
    candidate['ending']['fixed_narrations'] = candidate['mainline_chapters'][0]['fixed_narrations']
    issues = _validate_idea_outline(candidate, minimum=3, maximum=6)
    assert any(i['path'] == 'ending.fixed_narrations' for i in issues)


def test_ending_asset_repair_receives_entry_only_contract_and_preserves_text():
    candidate = display_outline()
    pieces = deepcopy(candidate['mainline_chapters'][0]['fixed_narrations'])
    candidate['ending']['fixed_narrations'] = deepcopy(pieces)
    pieces[0]['trigger'] = {'type': 'entry'}
    calls = []

    def model(messages, **options):
        request = json.loads(messages[1]['content'])
        calls.append(request)
        assert request['requested_paths'] == ['ending.fixed_narrations']
        contract = request['requested_replacements'][0]['output_contract']
        assert contract[0]['trigger'] == {'type': 'entry'}
        return json.dumps({'replacements': {'ending.fixed_narrations': pieces}}, ensure_ascii=False)

    result = NumericV2Generator(model_call=model).generate(
        title='结局原文修订', setup=_generation_setup(), checkpoint={'candidate': candidate})
    assert len(calls) == 1
    assert result['story']['nodes'][-1]['story_beat']['fixed_narrations'] == pieces

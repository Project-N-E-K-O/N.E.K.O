"""An instruction to another actor must not prove that actor's completed result."""

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2RuntimeError, validate_fact_candidates
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_evaluator_facts import _candidate
from tests.unit.theater_workshop.test_numeric_v2_generation import _idea_outline
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from tests.unit.theater_workshop.test_branch_completion_contract import _case
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, _validate_idea_outline


KEY = 'scene:start:door_open'


def _contract(*, restricted=True):
    return {'facts': {KEY: {'description': '角色已经打开门。', 'value_type': 'bool', 'visibility': 'public',
                           **({'evidence_source': 'performance'} if restricted else {})}}}


@pytest.mark.parametrize('source', ['player_input', 'runtime_fact'])
def test_performance_result_rejects_other_sources_even_with_verbatim_quote(source):
    text = '请你打开门。'
    with pytest.raises(NumericV2RuntimeError, match='evidence_source_not_allowed'):
        validate_fact_candidates([_candidate(quote=text, source=source)],
            fact_contract=_contract(), evidence_sources={source: text})


@pytest.mark.parametrize('restricted', [False, True])
def test_performance_result_accepts_actual_body_and_legacy_permissions_are_preserved(restricted):
    text = '角色推开了门，门后的楼梯清晰可见。'
    sources = ['actor_performance'] if restricted else ['actor_performance', 'player_input']
    for source in sources:
        ops, audit = validate_fact_candidates([_candidate(quote=text, source=source)],
            fact_contract=_contract(restricted=restricted), evidence_sources={source: text})
        assert ops[0]['value'] is True
        assert audit[0]['evidence'] == [{'source': source, 'quote': text}]


def test_compiler_and_runtime_preserve_source_restriction():
    story = numeric_v2_story()
    story['fact_contract'] = _contract()
    engine = NumericV2Engine.from_mapping(story)
    assert engine.fact_contract == _contract()['facts']
    assert engine.compiled.story['fact_contract'] == _contract()


@pytest.mark.parametrize('source', [None, '', 'player_input', ['performance'], True])
def test_invalid_source_policy_is_rejected(source):
    contract = _contract()
    contract['facts'][KEY]['evidence_source'] = source
    with pytest.raises(NumericV2RuntimeError, match='story_fact_contract_invalid'):
        validate_fact_candidates([], fact_contract=contract, evidence_sources={})


def test_workshop_mainline_exports_the_same_explicit_source_policy():
    outline = _idea_outline()
    fact = outline['mainline_chapters'][0]['completion_facts'][0]
    fact['evidence_source'] = 'performance'
    assert not _validate_idea_outline(outline, minimum=3, maximum=6, scene_expected_turns_target=8)
    story = NumericV2Generator()._project_story(title='来源验证', original_idea='旧信',
        setup=numeric_v2_setup(), outline=outline, tone=['克制'])
    key = 'scene:mainline_01:' + fact['id']
    assert NumericV2Engine.from_mapping(story).fact_contract[key]['evidence_source'] == 'performance'


def test_workshop_branch_exports_policy_without_restricting_player_choice():
    project, service, plan, result = _case()
    result['scenes'][0]['completion_facts'][1]['evidence_source'] = 'performance'
    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    definitions = NumericV2Engine.from_mapping(story).fact_contract
    prefix = 'scene:' + draft['scene_ids'][0] + ':'
    assert definitions[prefix + 'evidence_received']['evidence_source'] == 'performance'
    assert 'evidence_source' not in definitions[prefix + 'handoff_allowed']

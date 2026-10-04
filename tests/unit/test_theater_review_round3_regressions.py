"""Regressions reproduced in the final PR review, using production boundaries."""

from contextlib import nullcontext
import json

import pytest

from main_routers.numeric_theater_router import _performance_block_group
from services.theater.numeric_v2_identity import numeric_v2_catgirl_binding
from services.theater.numeric_v2_performance import performance_content_blocks
from theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore, NumericV2ProjectError
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from theater_workshop.host import InProcessPackageGateway
from utils import theater_activity
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from tests.unit.test_theater_numeric_v2_router import _client
from utils.config_manager.characters import CharactersMixin
from services.theater.numeric_v2_fixed_narration import apply_triggers
from tests.unit.test_theater_numeric_v2_fixed_narration import _engine, OPENING
from tests.unit.test_theater_numeric_v2_runtime import _binding
from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
from tests.unit.theater_workshop.test_numeric_v2_branch import branchable_project, _unconditional_route


def test_placeholder_filter_matches_tts_segment_offsets():
    performance = {'segments': [
        {'content': [{'type': 'dialogue', 'speaker_id': 'active_catgirl', 'text': '先走吧。'}]},
        {'phase': 'transition_bridge', 'content': [
            {'type': 'narration', 'text': '时间向前流转，现场随之转换。'}]},
        {'content': [{'type': 'dialogue', 'speaker_id': 'active_catgirl', 'text': '到了。'}]},
    ]}
    assert len(performance_content_blocks(performance)) == 2
    blocks, offset = _performance_block_group(performance, 1)
    assert offset == 1
    assert blocks[0]['text'] == '到了。'


@pytest.mark.parametrize('evidence', ['的', '我', '铭牌', '！！！'])
def test_fixed_narration_rejects_trivial_citations(evidence):
    engine = _engine()
    session = engine.create_session(session_id='short-evidence', catgirl_binding=_binding(), opening_performance=OPENING)
    result = apply_triggers(engine.nodes['start'], session,
        {'performance': '我看着桌上的铭牌。'}, ({'id': 'log', 'evidence': evidence},), '', known=False)
    assert result == {'performance': '我看着桌上的铭牌。'}


def test_upstream_diamond_unknown_condition_has_bounded_work(monkeypatch):
    project = branchable_project()
    story = project['story']
    story['start_node_id'] = 'diamond_0'
    for index in range(20):
        story['nodes'].append({'id': f'diamond_{index}', 'route_gates': [
            _unconditional_route(f'left_{index}', f'left_{index}', '左'),
            _unconditional_route(f'right_{index}', f'right_{index}', '右')]})
        for side in ('left', 'right'):
            story['nodes'].append({'id': f'{side}_{index}', 'route_gates': [
                _unconditional_route(f'join_{side}_{index}', f'diamond_{index+1}', '合流')]})
    final = _unconditional_route('unknown-entry', 'main_1', '目的地')
    final['conditions'] = {'all': [{'metric': 'courage', 'op': '>=', 'value': 10}]}
    story['nodes'].append({'id': 'diamond_20', 'route_gates': [final]})
    service = NumericV2BranchService()
    original = service._condition_alternatives
    calls = []
    def counted(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(service, '_condition_alternatives', counted)
    result = service._entry_scenarios(project, 'main_1', 'trust')
    assert not result['scenarios']
    assert result['unknown_reasons']
    assert len(calls) < 100


def test_equal_value_merged_entries_keep_both_route_paths():
    project = branchable_project()
    story = project['story']
    story['start_node_id'] = 'fork'
    story['nodes'].extend([
        {'id': 'fork', 'route_gates': [_unconditional_route('left', 'left', '左'), _unconditional_route('right', 'right', '右')]},
        {'id': 'left', 'route_gates': [_unconditional_route('left_join', 'join', '合流')]},
        {'id': 'right', 'route_gates': [_unconditional_route('right_join', 'join', '合流')]},
        {'id': 'join', 'route_gates': [_unconditional_route('entry', 'main_1', '目的地')]},
    ])
    result = NumericV2BranchService()._entry_scenarios(project, 'main_1', 'trust')
    assert {tuple(row['path']) for row in result['scenarios']} == {
        ('left', 'left_join', 'entry'), ('right', 'right_join', 'entry')}
    assert len({row['value'] for row in result['scenarios']}) == 1
    assert not result['unknown_reasons']


def test_setup_draft_rejects_new_unknown_keys_and_repairs_existing_ones(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    project = store.update(project['project_id'], base_revision=project['revision'],
                           changes={'setup': numeric_v2_setup()})
    with pytest.raises(NumericV2ProjectError, match='unsupported_setup_field'):
        store.update(project['project_id'], base_revision=project['revision'], changes={'setup': {'foo': 1}})
    path = store._path(project['project_id'])
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['setup']['foo'] = 'legacy unknown'
    raw['setup']['relationship'] = 'old value'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    repaired = store.update(project['project_id'], base_revision=project['revision'],
                            changes={'setup': {'relationship': None}})
    assert 'foo' not in repaired['setup']
    assert not repaired['setup'].get('relationship')
    assert repaired['setup']['brief'] == project['setup']['brief']


def test_current_binding_rejects_unpersisted_identity():
    class Manager:
        def load_character_binding_snapshot(self, name):
            raise PermissionError('write-back unavailable')
    with pytest.raises(ValueError, match='current_catgirl_identity_unavailable'):
        numeric_v2_catgirl_binding(Manager())


def test_current_persisted_card_ignores_unpersisted_unrelated_card(tmp_path):
    path = tmp_path / 'characters.json'
    card = {'_reserved': {'character_id': 'character_0123456789abcdef0123456789abcdef'}, '性格': '温和'}
    path.write_text(json.dumps({'猫娘': {'Lan': card, 'Other': {}}, '当前猫娘': 'Lan'}), encoding='utf-8')
    class Manager(CharactersMixin):
        def get_config_path(self, name):
            return path
        def load_characters(self):
            return {'猫娘': {'Lan': card, 'Other': {'_reserved': {'character_id': 'temporary'}}}, '当前猫娘': 'Lan'}
    assert Manager().load_character_binding_snapshot()['猫娘']['Lan'] == card
    card_without_id = {'性格': '温和'}
    path.write_text(json.dumps({'猫娘': {'Lan': card_without_id}, '当前猫娘': 'Lan'}), encoding='utf-8')
    with pytest.raises(ValueError, match='current_catgirl_identity_unavailable'):
        Manager().load_character_binding_snapshot()


def test_cancelled_start_cleanup_does_not_end_a_peer_claim(tmp_path, monkeypatch):
    theater_activity.clear_all_theater_activity()
    try:
        with _client(tmp_path, monkeypatch) as client:
            first = client.post('/api/theater-numeric/session/start',
                headers={'X-Neko-Theater-Activity': 'old-owner'},
                json={'story_id': 'numeric_v2_contract', 'session_id': 'cancelled-peer'})
            assert first.status_code == 200, first.text
            peer = client.get('/api/theater-numeric/session/cancelled-peer',
                params={'story_id': 'numeric_v2_contract'}, headers={'X-Neko-Theater-Activity': 'new-owner'})
            assert peer.json()['activity_claimed'] is True
            cleanup = client.post('/api/theater-numeric/session/end',
                headers={'X-Neko-Theater-Activity': 'old-owner'}, json={
                    'story_id': 'numeric_v2_contract', 'session_id': 'cancelled-peer',
                    'base_revision': 0, 'base_lifecycle_revision': 0, 'cancelled_start': True})
            assert cleanup.status_code == 409, cleanup.text
            assert cleanup.json()['reason'] == 'numeric_cancelled_start_taken_over'
            assert client.get('/api/theater-numeric/session/cancelled-peer',
                params={'story_id': 'numeric_v2_contract', 'claim_activity': False}).json()['session']['status'] == 'active'
    finally:
        theater_activity.clear_all_theater_activity()


@pytest.mark.parametrize('neutral', [True, False])
def test_interrupted_generation_keeps_original_error_and_only_valid_checkpoint(tmp_path, neutral):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    store.begin_generation(project['project_id'], base_revision=project['revision'])
    changes = {'editor': {'node_positions': {}}} if neutral else {'title': 'changed'}
    edited = store.update(project['project_id'], base_revision=project['revision'], changes=changes)
    checkpoint = {'candidate': {'outline': 'already generated'}}
    failed = store.fail_generation(project['project_id'], base_revision=project['revision'],
        error={'code': 'provider_failed'}, checkpoint=checkpoint, source_project=project)
    assert failed['revision'] == edited['revision']
    assert failed['generation_error']['original_error']['code'] == 'provider_failed'
    assert bool(store.generation_checkpoint(project['project_id'])) is neutral

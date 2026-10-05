"""Regression coverage for the missed maintainer review 5978450755."""

from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_workflow as workflow
from tests.unit.test_theater_review_round2_frontend import RUNTIME_HARNESS, _run
from tests.unit.test_theater_numeric_v2_router import _client
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore, _setup_fields
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from theater_workshop.host import InProcessPackageGateway
from tests.unit.theater_workshop.test_sdk_lifecycle import opened, ready_to_publish, setup_project  # noqa: F401
from theater_workshop.sdk import WorkshopError


@pytest.mark.parametrize('reason', ['user_exit', 'story_ending'])
def test_start_does_not_replace_user_ended_session(reason):
    _run(RUNTIME_HARNESS + '''
async function run() {
 const ctx = createContext();
 sendLaunch(ctx, 'session_a', 0, 'theater:start-request');
 for (let i=0; i<4; i++) await tick();
 const request = take(ctx, /\\/session\\/start$/);
 const ended = snapshot('session_a', 3, 'ended');
 ended.session.ended_reason = REASON;
 ended.end_receipt_id = 'retained-receipt';
 await respond(request, {...ended, resumed:true});
 assert.equal(ctx.requests.filter(r=>/\\/session\\/start$/.test(r.url)).length, 0);
 assert.equal(ctx.runtime.getState().sessionStatus, 'ended');
 assert.equal(ctx.runtime.getState().errorMessage, '');
 assert.equal(ctx.runtime.getState().pendingEnd.end_receipt_id, 'retained-receipt');
}
'''.replace('REASON', repr(reason)))


@pytest.mark.parametrize('offer', ['（指向长街）一起去长街吗？', '一起去长街吗？（指向长街）'])
def test_authored_dialogue_comparison_ignores_action_blocks(offer):
    assert workflow._authored_offer_visible({'performance': offer}, offer)
    assert not workflow._authored_offer_visible({'performance': '（指向长街）'}, offer)


def test_start_reads_user_exit_without_deleting_pending_receipt(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        scope = {'story_id':'numeric_v2_contract', 'session_id':'user-exited'}
        first = client.post('/api/theater-numeric/session/start', json=scope).json()
        ended = client.post('/api/theater-numeric/session/end', json={**scope,
            'base_revision':first['session']['revision'], 'base_lifecycle_revision':0}).json()
        resumed = client.post('/api/theater-numeric/session/start', json={
            **scope, 'session_id':'another-window'}).json()
        assert resumed['resumed'] and resumed['session']['ended_reason'] == 'user_exit'
        assert client.get('/api/theater-numeric/session/user-exited', params={'story_id':scope['story_id']}).status_code == 200
        skipped = client.post('/api/theater-numeric/session/archive/skip', json={
            **scope, 'revision':ended['session']['revision'], 'end_receipt_id':ended['end_receipt_id']})
        assert skipped.status_code == 200, skipped.text


@pytest.mark.parametrize('field,value,reason', [
    ('client_turn_id', None, 'client_turn_id_invalid'),
    ('client_turn_id', 'bad id', 'client_turn_id_invalid'),
    ('base_revision', '0', 'base_revision_invalid'),
    ('base_revision', True, 'base_revision_invalid'),
])
def test_input_validation_remains_structured_400(tmp_path, monkeypatch, field, value, reason):
    with _client(tmp_path, monkeypatch) as client:
        first = client.post('/api/theater-numeric/session/start', json={
            'story_id':'numeric_v2_contract', 'session_id':'validation'}).json()
        payload = {'story_id':'numeric_v2_contract', 'session_id':'validation',
                   'client_turn_id':'turn', 'base_revision':first['session']['revision'], 'message':'看看周围。'}
        payload[field] = value
        response = client.post('/api/theater-numeric/session/input', json=payload)
        assert response.status_code == 400, response.text
        assert response.json()['reason'] == reason


@pytest.mark.parametrize('field,value,reason', [('revision','0','revision_invalid'),
    ('node_turn_count',None,'node_turn_count_invalid'), ('current_node_id','bad id','current_node_id_invalid')])
@pytest.mark.parametrize('path', ['/session/validation', '/session/active'])
def test_corrupt_session_remains_structured_409(tmp_path, monkeypatch, field, value, reason, path):
    with _client(tmp_path, monkeypatch) as client:
        client.post('/api/theater-numeric/session/start', json={
            'story_id':'numeric_v2_contract', 'session_id':'validation'})
        saved = tmp_path / 'theater/numeric_v2/sessions/validation.json'
        payload = json.loads(saved.read_text(encoding='utf-8'))
        payload['session'][field] = value
        saved.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        response = client.get('/api/theater-numeric' + path, params={'story_id':'numeric_v2_contract'})
        assert response.status_code == 409, response.text
        assert response.json()['reason'] == reason


def test_binding_fence_maps_to_identity_reason():
    from services.theater.numeric_v2_identity import numeric_v2_catgirl_binding
    from utils.cloudsave_runtime import MaintenanceModeError
    class Config:
        def load_character_binding_snapshot(self, _):
            raise MaintenanceModeError('maintenance_readonly', target='characters.json')
    with pytest.raises(ValueError, match='^current_catgirl_identity_unavailable$'):
        numeric_v2_catgirl_binding(Config())


def test_legacy_bands_match_by_content_after_reorder():
    setup = numeric_v2_setup()
    for i, band in enumerate(setup['metrics'][0]['bands']):
        band['old_key'] = str(i)
    incoming = deepcopy(setup)
    incoming['metrics'][0]['bands'].reverse()
    clean = _setup_fields(incoming, legacy=setup)
    assert [b['label'] for b in clean['metrics'][0]['bands']] == [b['label'] for b in incoming['metrics'][0]['bands']]
    assert all('old_key' not in b for b in clean['metrics'][0]['bands'])
    assert _setup_fields(numeric_v2_setup(), legacy={'metrics':{}})['metrics']


def test_import_preserves_band_description(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    source = store.create()
    source['project_id'] = 'project_legacy_import'
    source['setup'] = numeric_v2_setup()
    source['setup']['metrics'][0]['bands'][0]['description'] = '作者写的区间说明'
    imported = store.import_project(source)
    assert imported['setup']['metrics'][0]['bands'][0]['description'] == '作者写的区间说明'


@pytest.mark.parametrize('metrics', [[{'id':str(i)} for i in range(5)],
    [{'id':'same'}, {'id':'same'}], [{}], ['bad metric']])
def test_generate_maps_draft_normalization_errors(opened, metrics):
    host, _ = opened
    project = setup_project(host.sdk)
    source = deepcopy(project)
    source['project_id'] = 'project_bad_metrics'
    source['setup']['metrics'] = metrics
    imported = host.sdk._store.import_project(source)
    with pytest.raises(WorkshopError, match='^generation_setup_invalid$'):
        host.sdk.generate(imported['project_id'], base_revision=imported['revision'])


def test_enhance_allows_last_draft_goal_without_id(opened, monkeypatch):
    from theater_workshop.sdk.numeric_v2 import goals_to_package
    from tests.unit.theater_workshop.test_numeric_v2_branch import _ordered_goal
    host, _ = opened
    project = ready_to_publish(host.sdk)
    story = deepcopy(project['story'])
    node = next(n for n in story['nodes'] if n.get('route_gates'))
    node['story_beat']['goals'] = goals_to_package(node['id'], [_ordered_goal('线索已经交付。')])
    previous = deepcopy(node['story_beat']['goals'])
    node['story_beat']['goals'][-1].pop('id')
    project = host.sdk.update_project(project['project_id'], base_revision=project['revision'], changes={'story':story})
    monkeypatch.setattr(host.sdk._generator, 'enhance_node', lambda **kwargs: {'goals':previous})
    result = host.sdk.enhance_node(project['project_id'], node['id'], base_revision=project['revision'])
    assert result['project']['story']['nodes']

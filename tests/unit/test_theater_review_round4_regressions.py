"""Regression coverage for PR review 5977083925 at production boundaries."""

from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_fixed_narration import apply_triggers
from tests.unit.test_theater_numeric_v2_fixed_narration import _engine, OPENING
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_prompt_permissions import _contract
from theater_workshop.sdk.numeric_v2_project_store import NumericV2ProjectStore, NumericV2ProjectError
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from theater_workshop.host import InProcessPackageGateway


@pytest.mark.parametrize('evidence,player', [('递给你', '我把纸条递给你'),
    ('抱住她', '（抱住她）'), ('抱抱', '抱抱'), ('抱抱', '（抱抱）'),
    ('抱抱', '抱抱！'), ('抱抱', '抱抱。'), ('AI助手', '看看AI助手'), ('好的ok', '好的ok。')])
def test_short_chinese_literal_evidence_is_delivered(evidence, player):
    story = deepcopy(_engine().story)
    # The citation fixture must actually satisfy this authored condition;
    # source/length validation is not a semantic condition solver.
    story['nodes'][0]['story_beat']['fixed_narrations'][1]['trigger']['condition'] = (
        '玩家已经把纸条递给猫娘。' if evidence == '递给你' else '玩家已经拥抱猫娘。'
    )
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id='short-chinese', catgirl_binding=_binding(), opening_performance=OPENING)
    result = apply_triggers(engine.nodes['start'], session, {'performance': '（点头）'},
        ({'id': 'log', 'evidence': evidence},), player, known=False)
    assert any(piece['id'] == 'log' for piece in result['fixed_narrations'])


@pytest.mark.parametrize('update', [{'brief': '新的概述'}, {}, None, {'foo': None}])
def test_legacy_nested_draft_keys_can_be_repaired_without_losing_metrics(tmp_path, update):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    project = store.update(project['project_id'], base_revision=project['revision'], changes={'setup': numeric_v2_setup()})
    path = store._path(project['project_id'])
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['setup']['foo'] = 'old'
    raw['setup']['metrics'][0]['unit'] = '点'
    raw['setup']['metrics'][0]['bands'][0]['foo'] = 'old band'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    incoming = raw['setup'] if update is None else update
    repaired = store.update(project['project_id'], base_revision=project['revision'], changes={'setup': incoming})
    metric = repaired['setup']['metrics'][0]
    assert 'foo' not in repaired['setup'] and 'unit' not in metric and 'foo' not in metric['bands'][0]
    assert metric['id'] == raw['setup']['metrics'][0]['id']
    assert metric['initial'] == raw['setup']['metrics'][0]['initial']


@pytest.mark.parametrize('location,reason', [('setup', 'unsupported_setup_field'),
    ('metric', 'unsupported_metric_field'), ('band', 'unsupported_metric_band_field')])
@pytest.mark.parametrize('importing', [False, True])
def test_new_nested_unknown_keys_rejected_on_update_and_import(tmp_path, location, reason, importing):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    setup = numeric_v2_setup()
    target = {'setup': setup, 'metric': setup['metrics'][0], 'band': setup['metrics'][0]['bands'][0]}[location]
    target['foo'] = 'new bad field'
    with pytest.raises(NumericV2ProjectError, match=reason):
        if importing:
            source = {**project, 'project_id': 'new-import', 'setup': setup}
            store.import_project(source)
        else:
            store.update(project['project_id'], base_revision=project['revision'], changes={'setup': setup})


@pytest.mark.asyncio
@pytest.mark.parametrize('silent,evaluator', [(False, True), (True, False)])
@pytest.mark.parametrize('invites,actor_flag,authored', [
    (True, True, '要现在一起去长街继续调查吗？'),
    (False, False, '（指向长街）要现在一起去长街继续调查吗？'),
    (False, False, '要现在一起去长街继续调查吗？（指向长街）'),
])
async def test_actor_invitation_not_duplicated_and_mute_scene_has_no_author_button(tmp_path, monkeypatch, silent, evaluator, invites, actor_flag, authored):
    story = numeric_v2_story()
    story['fact_contract'] = {'facts': {'scene:start:done': {
        'value_type': 'bool', 'visibility': 'public', 'description': '当前幕完成。'}}}
    story['nodes'][0]['completion_contract'] = {'all': [{'key': 'scene:start:done', 'equals': True}]}
    if silent:
        story['nodes'][0]['story_beat']['acting_contract'] = _contract('forbidden')
    route = story['nodes'][0]['route_gates'][1]
    route['transition_contract'].update(fallback_offer=authored, accept_input='好，我们现在过去。')
    middle = story['nodes'][2]
    middle.update(type='scene', min_turns=1)
    middle.pop('terminal')
    middle.pop('ending_id')
    middle['route_gates'] = [{'id': 'middle_leave', 'target_node_id': 'ending_after_middle', 'priority': 100,
        'conditions': {'all': []}, 'transition_contract': deepcopy(route['transition_contract'])}]
    story['nodes'].append({'id': 'ending_after_middle', 'type': 'ending', 'chapter': '离开',
        'story_beat': deepcopy(middle['story_beat']), 'route_gates': [], 'terminal': True, 'ending_id': 'leave'})
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id='single-offer', catgirl_binding=_binding(),
        opening_performance={'performance': '（点头）', 'suggested_inputs': []} if silent else _opening())
    outcome = runtime.prepare_turn(current, TurnRequestV2('done', 0, '解决了。'), (),
        fact_operations=({'op': 'set', 'key': 'scene:start:done', 'value': True, 'visibility': 'public'},))
    current = await runtime.commit_turn(outcome, {'performance': '（点头）' if silent else '问题解决了。',
        'suggested_inputs': [], 'transition_offered': False})
    actor_text = '（指向长街）' if silent else (
        '我们一起去长街继续调查吧？' if invites else '才、才没有紧张呢。只是有点累了。')
    async def options():
        from services.theater.numeric_v2_options import default_options
        return {**default_options(), 'review': False, 'evaluator': evaluator}
    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent='unclear')
    async def generate(self, **kwargs):
        return {'performance': actor_text, 'suggested_inputs': ['好啊，走吧。', '再歇一会儿。'], 'transition_offered': actor_flag}
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('invite', current.session.revision, '接下来呢？'), ensure_current_binding=lambda _: _binding())
    if not silent:
        assert authored in result.performance['performance']
        assert result.performance['suggested_inputs'][0] == '好，我们现在过去。'
        assert result.stored.session.transition_offered
        assert result.stored.ledger_events[-1]['program_invitation']['offer'] == authored
        return
    assert result.performance['performance'] == actor_text
    if actor_flag:
        assert result.performance['suggested_inputs'] == []
    else:
        assert result.performance['suggested_inputs'][0] == '好啊，走吧。'
    assert '好，我们现在过去。' not in result.performance['suggested_inputs']
    assert result.diagnostics['completion_fallback_offer_applied'] == 0


@pytest.mark.parametrize('reason,status', [('numeric_base_revision_mismatch', 409), ('catgirl_changed_requires_new_session', 400)])
def test_domain_reason_preserves_status_and_internal_detail_is_not_public(reason, status):
    from main_routers.numeric_theater_router import _domain_value_error
    assert _domain_value_error(ValueError(reason), status).status_code == status
    with pytest.raises(ValueError, match='internal detail'):
        _domain_value_error(ValueError('internal detail: /home/alice/secret.json'), status)


def test_start_after_cancelled_cleanup_can_replace_retired_slot(tmp_path, monkeypatch):
    from tests.unit.test_theater_numeric_v2_router import _client
    from utils import theater_activity
    theater_activity.clear_all_theater_activity()
    try:
        with _client(tmp_path, monkeypatch) as client:
            headers = {'X-Neko-Theater-Activity': 'cancelled-owner'}
            first = client.post('/api/theater-numeric/session/start', headers=headers, json={
                'story_id': 'numeric_v2_contract', 'session_id': 'cancelled-opening'}).json()
            retired = client.post('/api/theater-numeric/session/end', headers=headers, json={
                'story_id': 'numeric_v2_contract', 'session_id': 'cancelled-opening',
                'base_revision': first['session']['revision'], 'base_lifecycle_revision': 0,
                'cancelled_start': True})
            assert retired.status_code == 200, retired.text
            resumed = client.post('/api/theater-numeric/session/start',
                headers={'X-Neko-Theater-Activity': 'resuming-owner'}, json={
                'story_id': 'numeric_v2_contract', 'session_id': 'fresh-opening'}).json()
            assert resumed['resumed'] and resumed['session']['status'] == 'ended'
            fresh = client.post('/api/theater-numeric/session/start',
                headers={'X-Neko-Theater-Activity': 'replacement-owner'}, json={
                'story_id': 'numeric_v2_contract', 'session_id': 'fresh-opening', 'replace_existing': True})
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()['session']['status'] == 'active'
            assert fresh.json()['session']['session_id'] == 'fresh-opening'
            assert fresh.json()['activity_claimed'] is True
    finally:
        theater_activity.clear_all_theater_activity()


@pytest.mark.parametrize('method,path,payload', [
    ('get', '/session/active?story_id=numeric_v2_contract', None),
    ('get', '/session/safe?story_id=numeric_v2_contract', None),
    ('post', '/session/start', {'story_id': 'numeric_v2_contract', 'session_id': 'safe', 'character_id': 'safe'}),
    ('post', '/session/input', {'story_id': 'numeric_v2_contract', 'session_id': 'safe',
        'client_turn_id': 'turn', 'base_revision': 0, 'message': '看看周围。'}),
    ('post', '/session/end', {'story_id': 'numeric_v2_contract', 'session_id': 'safe',
        'base_revision': 0, 'base_lifecycle_revision': 0}),
    ('post', '/session/resume', {'story_id': 'numeric_v2_contract', 'session_id': 'safe',
        'base_revision': 0, 'base_lifecycle_revision': 0}),
])
def test_six_session_routes_do_not_echo_internal_value_error(tmp_path, monkeypatch, method, path, payload):
    from fastapi.testclient import TestClient
    from main_routers import numeric_theater_router as router
    from tests.unit.test_theater_numeric_v2_router import _client

    async def fail(*args, **kwargs):
        raise ValueError('internal detail: /home/alice/secret.json')
    with _client(tmp_path, monkeypatch) as setup_client:
        monkeypatch.setattr(router, '_runtime_for_story', fail)
        with TestClient(setup_client.app, raise_server_exceptions=False) as client:
            response = client.request(method, '/api/theater-numeric' + path, json=payload)
            assert response.status_code == 500, response.text
            assert 'alice' not in response.text and 'secret.json' not in response.text

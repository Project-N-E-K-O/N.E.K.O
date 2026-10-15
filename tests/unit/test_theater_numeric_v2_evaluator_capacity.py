"""Ordinary Evaluator capacity preserves fixed input and trims optional evidence."""
from copy import deepcopy
import asyncio
import json

import pytest

from services.theater import numeric_v2_evaluator as e
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


def data_from(messages):
    text = messages[-1].content
    return json.JSONDecoder().raw_decode(text[text.find('{'):])[0]


def tokens(messages):
    return sum(e.count_tokens(message.content) for message in messages)


def count_data(messages, data):
    prefix = messages[-1].content[:messages[-1].content.find('{')]
    return e.count_tokens(messages[0].content) + e.count_tokens(
        prefix + json.dumps(data, ensure_ascii=False, separators=(',', ':')))


def strip_quotes(state):
    state = deepcopy(state)
    count = 0
    for view in ('current', 'last_known'):
        for attributes in state[view].values():
            for attribute in attributes.values():
                if 'quote' in attribute:
                    attribute.pop('quote')
                    count += 1
    state['projection']['quotes_omitted'] += count
    return state


@pytest.fixture(params=['fantasy', 'scifi'])
def inputs(request, monkeypatch):
    engine, session, _, _ = _fixture(20)
    fantasy = request.param == 'fantasy'
    item = '晶石' if fantasy else '导航仪'
    position = '林缘入口' if fantasy else '舱门入口'
    message = '我继续观察晶石。' if fantasy else '我继续检查导航仪。'
    state = {
        'revision': 0, 'coverage_from': 0, 'coverage': 'partial',
        'current_node_id': session.current_node_id,
        'current': {'player': {'position': {
            'value': position, 'source_event': 'r0.opening.1',
            'known_to': ['player', 'character'], 'quote': '玩家已经站在入口等候。' * 50}}},
        'last_known': {'item:' + item: {'holder': {
            'value': 'character', 'source_event': 'r0.opening.2',
            'known_to': ['player'], 'quote': '角色手持物品，等待回应。' * 50}}},
        'recent_events': [{'event_id': 'r0.opening.1', 'subject': 'player',
                           'quote': '玩家已经站在入口等候。'}],
        'projection': {'retained_entities': 2, 'omitted_entities': 0,
                       'omitted_recent_events': 0, 'quotes_omitted': 3, 'over_budget': False},
    }
    latest = {'revision': 3, 'phase': 'turn', 'player_input': '我站在入口等。',
              'content': [{'type': 'dialogue', 'text': item + '仍由我拿着。'}]}
    monkeypatch.setattr(e, '_current_scene_context', lambda *args: deepcopy([latest]))
    monkeypatch.setattr(e, 'history_evidence', lambda *args, **kwargs: [])
    monkeypatch.setattr(e, 'project_narrative_state', lambda *args, **kwargs: deepcopy(state))
    profile = NUMERIC_V2_ACTOR_BUDGET_PROFILES[session.actor_budget_profile]
    monkeypatch.setitem(profile, 'evaluator_input_max_tokens', 100000)

    def build():
        diagnostics = {}
        messages = e._build_messages(engine, session, message,
            recent_ledger_events=(), diagnostics=diagnostics, allow_history_lookup=False)
        assert diagnostics['final_tokens'] == tokens(messages)
        return messages, data_from(messages), diagnostics

    def limit(value):
        monkeypatch.setitem(profile, 'evaluator_input_max_tokens', value)

    return engine, session, message, state, latest, build, limit


def test_exact_boundary_keeps_all_quotes_and_content(inputs):
    _,_,message,state,latest,build,limit=inputs
    messages,data,diag=build();limit(diag['final_tokens'])
    after,packed,_=build()
    assert [m.content for m in after]==[m.content for m in messages]
    assert packed['narrative_state']==state and packed['player_input']==message
    assert packed['scene_context']==[latest]

def test_optional_retrieval_trims_minimum_and_does_not_reappear(inputs,monkeypatch):
    *_,build,limit=inputs
    monkeypatch.setattr(e,'project_narrative_state',lambda *a,**k:None)
    rows=[{'revision':i,'source':'performance','current_visit':False,'text':str(i)+'此前公开的证据。'*100} for i in range(1,4)]
    original=deepcopy(rows)
    monkeypatch.setattr(e,'history_evidence',lambda *a,**k:deepcopy(rows))
    messages,data,_=build();expected=deepcopy(data);expected['history_evidence']=rows[1:]
    limit(count_data(messages,expected))
    _,packed,diag=build()
    assert packed==expected and diag['final_tokens']==diag['budget_tokens']
    assert rows==original

def test_history_trim_restores_evidence_from_removed_record(inputs,monkeypatch):
    *_,latest,build,limit=inputs
    monkeypatch.setattr(e,'project_narrative_state',lambda *a,**k:None)
    older={'revision':2,'phase':'turn','player_input':'此前玩家说了一段完整的话。'*200,'content':[{'type':'dialogue','text':'此前角色公开过这段路径。'}]}
    rows=[{'revision':2,'source':'performance','current_visit':True,'text':older['content'][0]['text']},
          {'revision':3,'source':'performance','current_visit':True,'text':latest['content'][0]['text']}]
    monkeypatch.setattr(e,'_current_scene_context',lambda *a:deepcopy([older,latest]))
    monkeypatch.setattr(e,'history_evidence',lambda *a,**k:deepcopy(rows))
    messages,data,_=build();assert 'history_evidence' not in data
    expected=deepcopy(data);expected['scene_context']=[latest];expected['history_evidence']=[rows[0]]
    prefix=messages[-1].content[:messages[-1].content.find('{')]
    # Restoring evidence also restores the evidence protocol in the system.
    desired=e.count_tokens(messages[0].content+e.HISTORY_EVIDENCE_RULE)+e.count_tokens(prefix+json.dumps(expected,ensure_ascii=False,separators=(',',':')))
    limit(desired)
    _,packed,diag=build()
    assert packed==expected and diag['recent_dropped_revisions']==[2]
    assert diag['final_tokens']==desired

def test_quote_pressure_only_omits_attribute_quotes(inputs):
    _,_,message,state,latest,build,limit=inputs
    messages,data,_=build();expected=deepcopy(data);expected['narrative_state']=strip_quotes(state)
    limit(count_data(messages,expected))
    _,packed,diag=build()
    assert packed==expected and diag['final_tokens']==diag['budget_tokens']
    assert packed['player_input']==message and packed['scene_context']==[latest]
    assert packed['narrative_state']['recent_events']==state['recent_events']
    assert state['projection']['quotes_omitted']==3

def test_still_over_budget_keeps_fixed_context_and_latest_record(inputs,monkeypatch):
    _,_,message,state,latest,build,limit=inputs
    rows=[{'revision':i,'source':'performance','current_visit':False,'text':'历史资料。'*50} for i in range(1,4)]
    monkeypatch.setattr(e,'history_evidence',lambda *a,**k:deepcopy(rows))
    messages,data,_=build();expected=deepcopy(data);expected.pop('history_evidence');expected['narrative_state']=strip_quotes(state)
    system=messages[0].content.replace(e.HISTORY_EVIDENCE_RULE,'',1)
    prefix=messages[-1].content[:messages[-1].content.find('{')]
    minimum=e.count_tokens(system)+e.count_tokens(prefix+json.dumps(expected,ensure_ascii=False,separators=(',',':')))
    limit(minimum-1)
    after,packed,diag=build()
    assert packed==expected and after[0].content==system
    assert diag['final_tokens']==minimum>diag['budget_tokens']
    assert packed['player_input']==message and packed['scene_context']==[latest]
    again,repeated,_=build()
    assert repeated==packed and [m.content for m in again]==[m.content for m in after]
    # The native caller must still reject an irreducible prompt before invoke.
    # The existing caller constructs a client before packing, so use a fake
    # context manager, never a real SDK constructor or network client.
    engine,session,*_=inputs
    invoked=[]
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
    async def config(*args,**kwargs):return {'model':'offline','base_url':'http://invalid.local'}
    async def factory(*args,**kwargs):return Client()
    async def forbidden_invoke(*args,**kwargs):invoked.append(True);raise AssertionError('over-budget request must not invoke')
    monkeypatch.setattr(e,'_model_config',config)
    monkeypatch.setattr(e,'create_chat_llm_async',factory)
    monkeypatch.setattr(e,'invoke_with_usage',forbidden_invoke)
    with pytest.raises(e.NumericV2EvaluatorError,match='numeric_v2_evaluator_input_budget_exceeded'):
        asyncio.run(e.NumericV2MetricEvaluator(object()).evaluate(engine=engine,session=session,message=message,allow_history_lookup=False))
    assert invoked==[]

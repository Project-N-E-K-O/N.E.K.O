"""Ordinary Evaluator capacity preserves fixed input and trims optional evidence."""
from copy import deepcopy
import asyncio

import pytest

from services.theater import numeric_v2_evaluator as e
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


def data_from(messages):
    text = messages[-1].content
    return prompt_payload(text)


def tokens(messages):
    return sum(e.count_tokens(message.content) for message in messages)


def count_data(messages, data):
    prefix = messages[-1].content.split("======以下为", 1)[0]
    return e.count_tokens(messages[0].content) + e.count_tokens(
        prefix + e.render_prompt_context(data))




@pytest.fixture(params=['fantasy', 'scifi'])
def inputs(request, monkeypatch):
    engine, session, _, _ = _fixture(20)
    fantasy = request.param == 'fantasy'
    item = '晶石' if fantasy else '导航仪'
    position = '林缘入口' if fantasy else '舱门入口'
    message = '我继续观察晶石。' if fantasy else '我继续检查导航仪。'
    latest = {'revision': 3, 'phase': 'turn', 'player_input': '我站在入口等。',
              'content': [{'type': 'dialogue', 'text': item + '仍由我拿着。'}]}
    monkeypatch.setattr(e, '_current_scene_context', lambda *args: deepcopy([latest]))
    monkeypatch.setattr(e, 'history_evidence', lambda *args, **kwargs: [])
    # These cases isolate dialogue/ledger packing; lifecycle pressure has its own cases below.
    monkeypatch.setattr(e, 'project_scene_facts', lambda *args: {
        'revision': session.revision, 'facts': [], 'truncated': False})
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

    return engine, session, message, latest, build, limit


def test_exact_boundary_keeps_all_quotes_and_content(inputs):
    _,_,message,latest,build,limit=inputs
    messages,data,diag=build();limit(diag['final_tokens'])
    after,packed,_=build()
    assert [m.content for m in after]==[m.content for m in messages]
    assert packed['player_input']==message
    assert packed['scene_context']==[latest]

def test_optional_retrieval_trims_minimum_and_does_not_reappear(inputs,monkeypatch):
    *_,build,limit=inputs
    rows=[{'revision':i,'source':'performance','current_visit':False,'text':str(i)+'此前公开的证据。'*100} for i in range(1,4)]
    original=deepcopy(rows)
    def fixed_retrieval(*args, **kwargs):
        # 本例只验证固定来源的最少裁剪；扩充阶段保留已经裁好的种子，不能绕过容量谓词。
        return deepcopy(kwargs['retained'] if 'within_prompt' in kwargs else rows)
    monkeypatch.setattr(e,'history_evidence',fixed_retrieval)
    messages,data,_=build();expected=deepcopy(data);expected['history_evidence']=rows[1:]
    limit(count_data(messages,expected))
    _,packed,diag=build()
    assert packed==expected and diag['final_tokens']==diag['budget_tokens']
    assert rows==original

def test_history_trim_restores_evidence_from_removed_record(inputs,monkeypatch):
    *_,latest,build,limit=inputs
    older={'revision':2,'phase':'turn','player_input':'此前玩家说了一段完整的话。'*200,'content':[
        {'type':'action','text':'指向路口'},{'type':'dialogue','text':'此前角色公开过这段路径。'}]}
    rows=[{'revision':2,'source':'performance','current_visit':True,'text':'（指向路口）此前角色公开过这段路径。'},
          {'revision':3,'source':'performance','current_visit':True,'text':latest['content'][0]['text']}]
    monkeypatch.setattr(e,'_current_scene_context',lambda *a:deepcopy([older,latest]))
    monkeypatch.setattr(e,'history_evidence',lambda *a,**k:deepcopy(rows))
    messages,data,_=build();assert 'history_evidence' not in data
    expected=deepcopy(data);expected['scene_context']=[latest];expected['history_evidence']=[rows[0]]
    prefix=messages[-1].content.split("======以下为", 1)[0]
    # Restoring evidence also restores the evidence protocol in the system.
    desired=e.count_tokens(messages[0].content+e.HISTORY_EVIDENCE_RULE)+e.count_tokens(prefix+e.render_prompt_context(expected))
    limit(desired)
    _,packed,diag=build()
    assert packed==expected and diag['recent_dropped_revisions']==[2]
    assert diag['final_tokens']==desired


@pytest.mark.parametrize('retrieve', [False, True])
def test_scored_input_is_sent_once_and_survives_history_eviction(inputs, monkeypatch, retrieve):
    engine, session, message, latest, _, limit = inputs
    original = '我核对左侧接口，并把红色指示灯的异常记录下来。'
    older = {'revision': 2, 'phase': 'turn', 'player_input': original,
             'content': [{'type': 'dialogue', 'text': '本轮完整的历史答复。' * 200}]}
    awards = [{"revision": 2, "metric_id": f"metric_{i}", "criterion_id": f"rule_{i}",
               "delta": 1, "input_text": original} for i in range(4)]
    source = {'revision': 2, 'source': 'player_input', 'current_visit': True, 'text': original}
    monkeypatch.setattr(e, '_current_scene_context', lambda *a: [older, latest])
    monkeypatch.setattr(e, '_recent_metric_awards', lambda *a: deepcopy(awards))
    monkeypatch.setattr(e, 'history_evidence', lambda *a, **k: [source] if retrieve else [])

    def build():
        return e._build_messages(engine, session, message)

    messages = build()
    data = data_from(messages)
    assert messages[1].content.count(original) == 1
    event, = data['recent_metric_awards']
    assert event['input_ref'] == 'scene_context[0].player_input'
    assert [change['metric_id'] for change in event['changes']] == [row['metric_id'] for row in awards]

    limit(tokens(messages) - 500)
    packed = build()
    data = data_from(packed)
    assert data['scene_context'] == [latest]
    assert packed[1].content.count(original) == 1
    event, = data['recent_metric_awards']
    if retrieve:
        assert event['input_ref'] == 'history_evidence[0].text'
    else:
        assert event['input_text'] == original


def test_still_over_budget_keeps_fixed_context_and_latest_record(inputs,monkeypatch):
    _,_,message,latest,build,limit=inputs
    rows=[{'revision':i,'source':'performance','current_visit':False,'text':'历史资料。'*50} for i in range(1,4)]
    monkeypatch.setattr(e,'history_evidence',lambda *a,**k:deepcopy(rows))
    messages,data,_=build();expected=deepcopy(data);expected.pop('history_evidence');pass
    system=messages[0].content.replace(e.HISTORY_EVIDENCE_RULE,'',1)
    prefix=messages[-1].content.split("======以下为", 1)[0]
    minimum=e.count_tokens(system)+e.count_tokens(prefix+e.render_prompt_context(expected))
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


def test_old_lifecycle_projection_yields_to_current_public_evidence(inputs, monkeypatch):
    _, session, message, latest, build, limit = inputs
    current = {'node_id': session.current_node_id, 'event_revision': 0, 'event': 'scene.entered'}
    projection = {'revision': session.revision, 'facts': [current], 'truncated': False}
    monkeypatch.setattr(e, 'project_scene_facts', lambda *a: deepcopy(projection))
    _, baseline, diag = build()
    limit(diag['final_tokens'] + 100)
    projection['facts'] = [
        {'node_id': f'previous_{i}', 'event_revision': 0, 'event': 'scene.completed'}
        for i in range(23)
    ] + [current]
    original = deepcopy(projection)
    _, packed, diag = build()
    summary = packed['current_story_beat']['runtime_scene_facts']
    assert diag['final_tokens'] <= diag['budget_tokens']
    assert summary['facts'] == [current] and summary['scope'] == 'current_visit'
    assert summary['truncated'] and summary['omitted_prior_scene_events'] == 23
    assert packed['scene_context'] == [latest] and packed['player_input'] == message
    assert packed['metrics'] == baseline['metrics'] and projection == original


def test_identical_ending_author_plan_is_kept_once_before_budget_check(inputs):
    engine, session, *_, build, limit = inputs
    engine.nodes[session.current_node_id]['story_beat']['narrative_summary'] = (
        '角色回应玩家立场，收好现场物品后安静告别；实际收束以后才进入结局。' * 25)
    messages, data, _ = build()
    ending = data['transition_preview']['natural_ending_context']
    assert ending['source_direction'] == (
        '见 current_story_beat.scene_direction；同一作者规划，不是已发生事实。')
    assert messages[1].content.count(data['current_story_beat']['scene_direction']) == 1
    limit(count_data(messages, data))
    _, packed, diag = build()
    assert packed == data and diag['final_tokens'] == diag['budget_tokens']


def test_ending_prompt_keeps_shared_neutral_content_once_and_preserves_stage_boundaries(inputs):
    engine, session, *_, build, _ = inputs
    source = engine.nodes[session.current_node_id]
    route = engine.preview_route(session.current_node_id, session.metrics, facts=session.story_state["facts"])
    ending = engine.nodes[route["target_node_id"]]
    shared = "由实际演出的递交结果继续收束，玩家拿着物品时继续保留原持有状态。"
    overlap = "确认灯光颜色"
    genuine_prohibition = "凭空增加第三个人"
    source["story_beat"]["content_boundaries"] = [shared, overlap, "回应来源幕刚发生的互动。"]
    source["story_beat"]["must_not_happen"] = [genuine_prohibition]
    ending["story_beat"]["content_boundaries"] = [shared, overlap]
    ending["story_beat"]["must_not_happen"] = [overlap, genuine_prohibition]
    ending["story_beat"]["opening_only_boundaries"] = ["保留进门时的位置。"]

    messages, packed, _ = build()
    context = packed["transition_preview"]["natural_ending_context"]
    assert messages[1].content.count(shared) == 1
    assert context["shared_content_boundaries"] == [shared]
    assert "按原条件同时适用于来源幕与结局" in messages[0].content
    assert context["source_boundaries"] == [overlap, "回应来源幕刚发生的互动。", f"禁止发生的剧情：{genuine_prohibition}"]
    assert context["ending_boundaries"] == [
        "保留进门时的位置。", f"禁止发生的剧情：{overlap}", f"禁止发生的剧情：{genuine_prohibition}",
    ]


def test_identical_author_opening_references_retained_playback_and_survives_eviction(inputs, monkeypatch):
    engine, session, _, latest, build, limit = inputs
    original = "天色渐暗，门廊里只剩一盏摇晃的小灯。" * 10
    engine.nodes[session.current_node_id]["story_beat"]["opening_scene"] = original
    opening = {"revision": 2, "phase": "scene_entry", "player_input": "", "content": [
        {"type": "narration", "text": original},
        {"type": "dialogue", "text": "可见的旧幕记录。" * 200},
    ]}
    monkeypatch.setattr(e, "_current_scene_context", lambda *args: deepcopy([opening, latest]))
    messages, packed, _ = build()
    assert messages[1].content.count(original) == 1
    assert packed["current_story_beat"]["scene_anchor"] == (
        "作者入幕规划，与 scene_context[0].content[0].text 同文。"
    )
    assert packed["scene_context"][0] == opening

    expected = deepcopy(packed)
    expected["scene_context"] = [latest]
    expected["current_story_beat"]["scene_anchor"] = original
    limit(count_data(messages, expected))
    _, packed, diagnostics = build()
    assert packed == expected
    assert diagnostics["recent_dropped_revisions"] == [2]




@pytest.mark.parametrize("deduplicate", [True, False])
def test_evaluator_deduplicates_only_exact_consecutive_blocks_in_the_same_visit(deduplicate):
    blocks = [
        {"type": "narration", "text": "雨仍在下。"},
        {"type": "action", "text": "指向路口"},
        {"type": "dialogue", "text": "从那里进站。"},
        {"type": "action", "text": "让开一步"},
    ]
    data = {"scene_context": [{"revision": 2, "phase": "scene_entry", "content": blocks}]}
    exact = {"revision": 2, "source": "performance", "current_visit": True, "text": "（指向路口）从那里进站。"}
    distinct = [
        {**exact, "text": "（指向路口）（让开一步）"},
        {**exact, "current_visit": False},
        {**exact, "revision": 1},
        {**exact, "text": "（指向路口）不要从那里进站。"},
    ]
    e._deduplicate_packed_history_evidence(data, [exact, *distinct], deduplicate_performance_blocks=deduplicate)
    assert data["history_evidence"] == (distinct if deduplicate else [exact, *distinct])


def test_auxiliary_lifecycle_summary_can_yield_without_losing_current_result(inputs, monkeypatch):
    engine, session, message, latest, build, limit = inputs
    engine.fact_contract["prop:status"] = {"value_type": "string", "visibility": "public"}
    projection = {'revision': session.revision, 'facts': [
        {'node_id': session.current_node_id, 'event_revision': 0, 'event': 'scene.entered',
         'key': f'event:scene.entered:{session.current_node_id}:r0', 'value': True,
         'source_revision': 0, 'updated_revision': 0}], 'truncated': False}
    monkeypatch.setattr(e, 'project_scene_facts', lambda *a: deepcopy(projection))
    messages, data, _ = build()
    expected = deepcopy(data)
    assert expected['current_story_beat']['fact_candidate_sources'] == ["player_input", "runtime_fact"]
    expected['current_story_beat']['runtime_scene_facts'] = {
        'truncated': True, 'scope': 'omitted_for_capacity'}
    expected['current_story_beat']['fact_candidate_sources'] = ["player_input"]
    limit(count_data(messages, expected))
    _, packed, diag = build()
    assert packed == expected and diag['final_tokens'] == diag['budget_tokens']
    assert packed['player_input'] == message and packed['scene_context'] == [latest]
    assert projection['facts'][0]['event'] == 'scene.entered'

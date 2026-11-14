"""Exercise source-preserving transition generation through the real Actor transport/parser path."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, _binding, INPUT, KEY
from tests.utils.theater_prompt import prompt_payload


_CONFIG = {
    'model': 'qwen3.8-flash', 'provider_type': 'openai_compatible',
    'base_url': 'https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1',
}
_ENTRY = '程序原文：清单上的六项仍保持刚才确定的去向。'
_TAIL_FIELDS = {'bridge_scene_narration', 'target_scene_narration', 'target_performance', 'suggested_inputs'}


async def _case(tmp_path, *, target_policy='required', fixed_entry=False):
    engine = _engine()
    engine.nodes['start']['story_beat']['fixed_narrations'] = [{
        'id': 'source_receipt', 'text': '作者原文：这份清单已由两人当面确认。',
        'trigger': {'type': 'condition', 'condition': '双方同意同一份清单。'},
        'after': [], 'required_before_exit': False,
    }]
    target = engine.nodes['work']['story_beat']
    target['opening_scene'] = '清点台另一侧摆着空纸箱，接下来的搬运尚未开始。'
    target['acting_contract'] = {'dialogue_policy': target_policy}
    if fixed_entry:
        target['fixed_narrations'] = [{
            'id': 'program_entry', 'text': _ENTRY, 'trigger': {'type': 'entry'},
            'after': [], 'required_before_exit': True,
        }]
        target['entry_player_inputs'] = ['（查看登记表）核对已经定好的清单。']
        engine.nodes['start']['route_gates'][0]['transition_contract']['fixed_entry_only'] = True
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id='reviewed-source-tail', catgirl_binding=_binding(),
        opening_performance={'performance': '先看清单，去向还没有定。', 'suggested_inputs': []},
    )
    prefix = runtime.prepare_turn(current, TurnRequestV2('prefix', 0, '先看看清单。'), ())
    current = await runtime.commit_turn(prefix, {
        'performance': '（翻开登记表）上一批的编号在这里，先对照着看。', 'suggested_inputs': [],
    })
    request = TurnRequestV2('source-and-tail', current.session.revision, INPUT)
    outcome = runtime.prepare_turn(current, request, (), scene_complete=True, fact_operations=({
        'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public',
    },))
    assert outcome.session.current_node_id == 'work'
    assert outcome.session.dialogue_policy == target_policy
    source = {
        'scene_narration': '值班员核对完登记表：“编号K-17，记录无误。”',
        'performance': '（把清单压在蓝夹下）没意见，就按你刚才的新去向办。\n六件的标签保持原样。',
        'fixed_narrations': [{
            'node_id': 'start', 'id': 'source_receipt', 'text': '作者原文：这份清单已由两人当面确认。',
            'position': 'after', 'bindings': {
                'catgirl_name': _binding()['catgirl_name'], 'player_name': _binding()['player_address'],
            },
        }],
    }
    return runtime, current, outcome, source


def _transport(monkeypatch, payload):
    seen = []

    class Client:
        model = _CONFIG['model']

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            assert not seen, 'A continuation must not silently issue an unrelated extra Actor call.'
            seen.append({'messages': messages, 'schema': kwargs['response_format']['json_schema']['schema']})
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False), response_metadata={})

    async def config(_):
        return _CONFIG

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(actor, '_model_config', config)
    monkeypatch.setattr(actor, 'create_chat_llm_async', factory)
    return seen


def _tail(target_policy='required', *, fixed_entry=False):
    return {
        'bridge_scene_narration': '',
        'target_scene_narration': '' if fixed_entry else '清点台另一侧的纸箱上贴着六张空白标签。',
        'target_performance': '' if fixed_entry else (
            '（把纸箱转向桌边）' if target_policy == 'forbidden' else '接下来先处理最轻的几件。'),
        'suggested_inputs': [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('target_policy,fixed_entry', [
    ('required', False), ('forbidden', False), ('forbidden', True),
])
async def test_tail_wire_keeps_reviewed_source_and_only_generates_the_continuation(
    tmp_path, monkeypatch, target_policy, fixed_entry,
):
    runtime, current, outcome, source = await _case(
        tmp_path, target_policy=target_policy, fixed_entry=fixed_entry,
    )
    before = deepcopy((current, outcome, source))
    stored_bytes = runtime.store._path(current.session.session_id).read_bytes()
    payload = _tail(target_policy, fixed_entry=fixed_entry)
    seen = _transport(monkeypatch, payload)
    instance = actor.NumericV2Actor(object())

    result = await instance.generate_turn(engine=runtime.engine, session=current.session, outcome=outcome,
        player_input=INPUT, character_profile='温和克制。', transition_source=source)

    assert instance.provider_call_count == len(seen) == 1
    schema = seen[0]['schema']
    assert set(schema['properties']) == set(schema['required']) == _TAIL_FIELDS
    assert schema['additionalProperties'] is False
    Draft202012Validator(schema).validate(payload)
    if fixed_entry:
        assert schema['properties']['target_performance']['enum'] == ['']
        assert schema['properties']['target_scene_narration']['enum'] == ['']
    data = prompt_payload(seen[0]['messages'][1].content)
    assert data['player_input'] == INPUT
    assert data['transition']['reviewed_source'] == source
    assert source['performance'] not in json.dumps(data['recent_context'], ensure_ascii=False)
    assert '上一批的编号在这里' in json.dumps(data['recent_context'], ensure_ascii=False)
    assert result['segments'][0] == {'phase': 'source_response', **source}
    assert result['segments'][1]['scene_narration'] == ''
    assert result['segments'][2]['performance'] == payload['target_performance']
    if fixed_entry:
        assert [piece['text'] for piece in result['segments'][2]['fixed_narrations']] == [_ENTRY]
    else:
        assert result['segments'][2]['scene_narration'] == payload['target_scene_narration']
    assert (current, outcome, source) == before
    assert runtime.store._path(current.session.session_id).read_bytes() == stored_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['source_performance', 'source_scene_narration'])
async def test_tail_transport_rejects_a_model_attempt_to_replace_source(tmp_path, monkeypatch, field):
    runtime, current, outcome, source = await _case(tmp_path)
    untouched = deepcopy((current, outcome, source))
    payload = {**_tail(), field: '模型企图覆盖已经审核的来源原文。'}
    seen = _transport(monkeypatch, payload)

    with pytest.raises(actor.NumericV2ActorOutputError, match='transition_tail_invalid'):
        await actor.NumericV2Actor(object()).generate_turn(
            engine=runtime.engine, session=current.session, outcome=outcome,
            player_input=INPUT, character_profile='温和克制。', transition_source=source,
        )

    assert len(seen) == 1
    assert not Draft202012Validator(seen[0]['schema']).is_valid(payload)
    assert (current, outcome, source) == untouched


@pytest.mark.asyncio
async def test_tail_reviewed_source_cannot_be_trimmed_to_fit_the_prompt(tmp_path, monkeypatch):
    runtime, current, outcome, source = await _case(tmp_path)
    source['performance'] += '这段是必须完整保留的已审来源原文。' * 4000
    untouched = deepcopy((current, outcome, source))
    seen = _transport(monkeypatch, _tail())

    with pytest.raises(actor.NumericV2ActorError, match='budget_exceeded'):
        await actor.NumericV2Actor(object()).generate_turn(
            engine=runtime.engine, session=current.session, outcome=outcome,
            player_input=INPUT, character_profile='温和克制。', transition_source=source,
        )

    assert seen == []
    assert (current, outcome, source) == untouched

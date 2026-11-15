"""Public branch drafting corrects one candidate without changing its author inputs."""

from tests.utils.theater_prompt import prompt_payload

from contextlib import contextmanager, nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater_workshop.host import InProcessPackageGateway, NekoWorkshopModel, _with_workshop_watermark
from services.theater_workshop.sdk import (
    LLMCallFailure, ModelReply, NumericV2RevisionConflictError, TheaterWorkshop, WorkshopError,
)
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError, NumericV2Generator
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError
from utils.tokenize import count_tokens

from .test_numeric_v2_branch import (
    _character_state, _ending_goal, _path_result_from_context, branchable_project,
)


REJECTED = '只在被拒首稿出现的候选内容甲七'
ORIGINAL = '第一行：钟停了。\n第二行：编号003。'


def _ending_reply():
    return {
        'title': '档案留存', 'summary': '证据已经公开，两人完成记录。',
        'opening_scene': '公开后的档案静静放在桌面上。',
        'ordered_goals': [_ending_goal('结局开场展示已经公开的档案。')],
        'irreversible_facts': ['证据已经公开。'], 'character_state': _character_state(),
        'catgirl_situation': '她已完成记录，仍保留自己的边界。', 'tone': '克制',
    }


def _valid_reply(context):
    result = _path_result_from_context(context)
    scene = result['scenes'][0]
    scene['completion_facts'] = [{
        'id': 'evidence_shown', 'description': '猫娘已展示带日期的证据。',
        'value_type': 'bool', 'target_value': True, 'visibility': 'public',
        'evidence_source': 'performance',
    }]
    result['transitions'][1].update(trigger_fact_ids=['evidence_shown'], bridge_scene_narration='')
    return result


def _with_original(result):
    result['scenes'][0]['fixed_narrations'] = [{
        'id': 'author_log', 'text_source': {'start_after': '[原文起]', 'end_before': '[原文止]'},
        'trigger': {'type': 'entry'}, 'after': [], 'required_before_exit': False,
    }]
    return result


@pytest.fixture
def workshop(tmp_path):
    state = SimpleNamespace(calls=[], reply=lambda context, attempt: _valid_reply(context))

    def model(messages, **options):
        if options['operation'] == 'numeric_v2_branch_ending':
            return ModelReply(json.dumps(_ending_reply(), ensure_ascii=False), 'fixture-model')
        assert options['operation'] == 'numeric_v2_branch_path'
        assert options['max_retries'] == 1
        context = prompt_payload(messages[1]['content'])
        state.calls.append({'messages': deepcopy(messages), 'context': deepcopy(context)})
        assert state.path.read_bytes() == state.before_bytes
        reply = state.reply(context, len(state.calls))
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, LLMCallFailure):
            return ModelReply(reply, 'fixture-model')
        return ModelReply(json.dumps(reply, ensure_ascii=False), 'fixture-model')

    sdk = TheaterWorkshop.open(
        project_root=tmp_path / 'projects', control_root=tmp_path / 'control',
        gateway=InProcessPackageGateway(), model_call=model, write_transaction=nullcontext,
        authoring_names=lambda: None, token_counter=count_tokens,
    )
    try:
        project = sdk.import_story(branchable_project()['story'])
        source = 'main_3'
        options = sdk.branch_options(project['project_id'], source)
        key = next(row['key'] for row in options['condition_candidates'] if row['available'])
        ending = sdk.draft_branch_ending(
            project['project_id'], base_revision=project['revision'], source_node_id=source,
            ending_direction='公开记录后自然收束，保留玩家自主。',
            condition_selection={'mode': 'fixed', 'key': key},
        )['draft']
        state.sdk, state.project, state.ending = sdk, sdk.get_project(project['project_id']), ending
        state.path = sdk.root / (project['project_id'] + '.json')
        state.before_bytes = state.path.read_bytes()
        yield state
    finally:
        sdk.close()


def _draft(state, *, direction='先核对一份证据，再进入公开后的结局。'):
    return state.sdk.draft_branch_path(
        state.project['project_id'], base_revision=state.project['revision'],
        endpoint_mode='new_ending', length=1, direction=direction,
        ending_draft_id=state.ending['draft_id'], ending=deepcopy(state.ending['ending']),
    )['draft']


def _unchanged(state):
    assert state.path.read_bytes() == state.before_bytes
    assert state.sdk.get_project(state.project['project_id']) == state.project


def _same_inputs_with_feedback(state):
    assert len(state.calls) == 2
    first, second = [deepcopy(row['context']) for row in state.calls]
    assert 'validation_feedback' not in first
    feedback = second.pop('validation_feedback')
    assert set(feedback) <= {'code', 'path', 'issues'}
    assert feedback['code'] and feedback['issues']
    assert second == first
    assert REJECTED not in json.dumps(state.calls[1]['messages'], ensure_ascii=False)


@pytest.mark.parametrize('damage', ['bridge', 'offer', 'accept', 'player_opening', 'endpoint_copy'])
def test_public_path_corrects_only_the_failed_candidate_and_saves_once(workshop, damage):
    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            if damage in {'bridge', 'offer', 'accept'}:
                field = {'bridge': 'bridge_scene_narration', 'offer': 'fallback_offer', 'accept': 'accept_input'}[damage]
                result['transitions'][0][field] = ''
            elif damage == 'player_opening':
                result['scenes'][0]['ordered_goals'][0].update(
                    owner='player', delivery_type='player_action', sources=['opening'], timing='opening')
            else:
                endpoint = workshop.ending['ending']
                result['scenes'][0].update(opening_scene=endpoint['opening_scene'],
                                          ordered_goals=deepcopy(endpoint['ordered_goals']))
        return result

    workshop.reply = reply
    draft = _draft(workshop)
    _same_inputs_with_feedback(workshop)
    current = workshop.sdk.get_project(workshop.project['project_id'])
    assert current['story'] == workshop.project['story']
    assert current['revision'] == workshop.project['revision']
    assert set(current['authoring']['branch_drafts']) == {workshop.ending['draft_id'], draft['draft_id']}
    assert draft['source_node_id'] == workshop.ending['source_node_id']
    assert draft['new_ending'] == workshop.ending['ending']
    assert draft['scenes'][0]['completion_facts'][0]['evidence_source'] == 'performance'
    assert draft['transitions'][-1]['bridge_scene_narration'] == ''
    assert not draft['transitions'][-1].get('fallback_offer')
    assert not draft['transitions'][-1].get('accept_input')


@pytest.mark.parametrize('second_damage', ['bridge', 'fixed_original'])
def test_generation_and_contract_failures_share_two_attempts_and_never_write(workshop, second_damage):
    direction = f'第一幕逐字展示[原文起]{ORIGINAL}[原文止]'

    def reply(context, attempt):
        result = _with_original(_valid_reply(context))
        result['scenes'][0]['summary'] = REJECTED
        if attempt == 1 or second_damage == 'bridge':
            result['transitions'][0]['bridge_scene_narration'] = ''
        else:
            result['scenes'][0]['fixed_narrations'][0]['text_source']['end_before'] = '不存在的边界'
        return result

    workshop.reply = reply
    with pytest.raises((NumericV2GenerationError, NumericV2BranchError)):
        _draft(workshop, direction=direction)
    _same_inputs_with_feedback(workshop)
    _unchanged(workshop)


@pytest.mark.parametrize('code', ['model_timeout', 'model_provider_error', 'workshop_model_input_budget_exceeded'])
def test_technical_failure_is_not_retried_or_saved(workshop, code):
    failure = (WorkshopError(code) if code == 'workshop_model_input_budget_exceeded' else
               LLMCallFailure('provider unavailable', error_code=code, exception_type='InjectedFailure'))
    workshop.reply = lambda context, attempt: failure
    with pytest.raises((NumericV2GenerationError, WorkshopError)) as caught:
        _draft(workshop)
    assert caught.value.code == code
    assert len(workshop.calls) == 1
    _unchanged(workshop)


def test_correction_preserves_author_original_text_and_cannot_rewrite_endpoint(workshop):
    direction = f'第一幕逐字展示[原文起]{ORIGINAL}[原文止]'

    def reply(context, attempt):
        result = _with_original(_valid_reply(context))
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            result['transitions'][0]['fallback_offer'] = ''
        return result

    workshop.reply = reply
    draft = _draft(workshop, direction=direction)
    _same_inputs_with_feedback(workshop)
    piece, = draft['scenes'][0]['fixed_narrations']
    assert piece['text'] == ORIGINAL
    assert 'text_source' not in piece
    assert draft['new_ending'] == workshop.ending['ending']
    assert workshop.sdk.get_project(workshop.project['project_id'])['story'] == workshop.project['story']


def test_model_condition_trigger_can_be_corrected_to_the_authored_entry_original(workshop):
    direction = f'第一幕入幕逐字展示[原文起]{ORIGINAL}[原文止]'

    def reply(context, attempt):
        result = _with_original(_valid_reply(context))
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            result['scenes'][0]['fixed_narrations'][0]['trigger'] = {
                'type': 'condition', 'condition': '玩家已经阅读日志。', 'player_handoff_required': False,
            }
        return result

    workshop.reply = reply
    draft = _draft(workshop, direction=direction)
    _same_inputs_with_feedback(workshop)
    assert workshop.calls[1]['context']['validation_feedback']['code'] == 'invalid_model_json'
    piece, = draft['scenes'][0]['fixed_narrations']
    assert piece['text'] == ORIGINAL
    assert piece['trigger'] == {'type': 'entry'}
    assert draft['new_ending'] == workshop.ending['ending']
    current = workshop.sdk.get_project(workshop.project['project_id'])
    assert current['story'] == workshop.project['story']
    assert current['revision'] == workshop.project['revision']


def test_player_goal_after_another_goal_still_requires_its_own_input_evidence(workshop):
    description = '玩家决定把已展示的证据列入公开记录。'

    def reply(context, attempt):
        result = _valid_reply(context)
        scene = result['scenes'][0]
        scene['ordered_goals'].append({
            'owner': 'player', 'delivery_type': 'semantic_state', 'description': description,
            'evidence_mode': 'semantic', 'anchors': [], 'timing': 'turn',
            'sources': ['previous_goal'] if attempt == 1 else ['player_input'],
            'dialogue_policy_after': 'unchanged',
        })
        scene['completion_facts'].append({
            'id': 'recording_decided', 'description': description,
            'value_type': 'bool', 'target_value': True, 'visibility': 'public',
            'evidence_source': 'player_input',
        })
        result['transitions'][1]['trigger_fact_ids'].append('recording_decided')
        if attempt == 1:
            scene['summary'] = REJECTED
        return result

    workshop.reply = reply
    draft = _draft(workshop)
    _same_inputs_with_feedback(workshop)
    feedback = workshop.calls[1]['context']['validation_feedback']
    assert 'player_input' in json.dumps(feedback, ensure_ascii=False)
    assert any(issue.get('path') == 'scenes[0].ordered_goals[1].sources' for issue in feedback['issues'])
    goals = draft['scenes'][0]['ordered_goals']
    assert len(goals) == 2
    assert goals[1]['description'] == description
    assert goals[1]['owner'] == 'player' and goals[1]['timing'] == 'turn'
    assert goals[1]['sources'] == ['player_input']
    assert draft['transitions'][1]['trigger_fact_ids'] == ['evidence_shown', 'recording_decided']


def test_valid_first_candidate_keeps_empty_terminal_bridge_without_a_retry(workshop):
    draft = _draft(workshop)
    assert len(workshop.calls) == 1
    assert 'validation_feedback' not in workshop.calls[0]['context']
    assert draft['transitions'][0]['bridge_scene_narration']
    assert draft['transitions'][0]['fallback_offer']
    assert draft['transitions'][0]['accept_input']
    assert draft['transitions'][-1]['bridge_scene_narration'] == ''


def test_first_goal_cannot_use_a_previous_goal_from_the_source_scene(workshop):
    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            result['scenes'][0]['ordered_goals'][0]['sources'] = ['previous_goal']
        else:
            issue, = context['validation_feedback']['issues']
            assert issue['path'] == 'scenes[0].ordered_goals[0].sources'
            assert 'previous_goal' in issue['message'] and '首项' in issue['message']
            result['scenes'][0]['ordered_goals'][0]['sources'] = ['opening']
        return result

    workshop.reply = reply
    draft = _draft(workshop)
    _same_inputs_with_feedback(workshop)
    assert draft['scenes'][0]['ordered_goals'][0]['sources'] == ['opening']


def test_state_prefix_feedback_explains_the_constraint_without_rewriting_state(workshop):
    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            result['scenes'][0]['character_state']['player_state'] = '站在桌边，等待档案。'
        else:
            issue, = context['validation_feedback']['issues']
            assert issue['path'] == 'scenes[0].character_state.player_state'
            prefix = context['global']['intro'].get('player_name', '男主')
            assert issue['expected_prefix'] == prefix and prefix in issue['message']
            result['scenes'][0]['character_state']['player_state'] = prefix + '站在桌边，等待档案。'
        return result

    workshop.reply = reply
    draft = _draft(workshop)
    _same_inputs_with_feedback(workshop)
    assert draft['scenes'][0]['character_state']['player_state'].endswith('站在桌边，等待档案。')


@pytest.mark.parametrize('damage', ['subject', 'dialogue'])
def test_one_correction_receives_independent_state_and_terminal_mode_errors(workshop, damage):
    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['scenes'][0]['summary'] = REJECTED
            state = result['scenes'][0]['character_state']
            if damage == 'subject':
                state['player_state'] = '站在桌边，等待档案。'
            else:
                state['acting_contract']['dialogue_policy'] = 'forbidden'
            result['transitions'][1]['advance_on_completion'] = True
        else:
            issues = context['validation_feedback']['issues']
            assert {issue['path'] for issue in issues} == {
                ('scenes[0].character_state.player_state' if damage == 'subject'
                 else 'scenes[0].character_state.acting_contract.dialogue_policy'),
                'transitions[1].advance_on_completion',
            }
            assert all(issue.get('message') or issue.get('issue_code') for issue in issues)
        return result

    workshop.reply = reply
    draft = _draft(workshop)
    _same_inputs_with_feedback(workshop)
    assert draft['scenes'][0]['character_state']['acting_contract']['dialogue_policy'] != 'forbidden'
    assert draft['transitions'][1].get('advance_on_completion', False) is False


@pytest.mark.parametrize('text,expected', [
    ('直播间只剩桌上一盏灯。', '环境：直播间只剩桌上一盏灯。'),
    ('环境中只剩桌上一盏灯。', '环境中只剩桌上一盏灯。'),
])
def test_fresh_environment_label_is_mechanical_and_uses_no_correction(workshop, text, expected):
    replies = []
    def reply(context, attempt):
        result = _valid_reply(context)
        result['scenes'][0]['character_state']['environment_state'] = text
        replies.append(result)
        return result
    workshop.reply = reply
    draft = _draft(workshop)
    assert len(workshop.calls) == 1
    assert draft['scenes'][0]['character_state']['environment_state'] == expected
    assert replies[0]['scenes'][0]['character_state']['environment_state'] == text


def test_environment_label_does_not_invent_missing_description(workshop):
    def reply(context, attempt):
        result = _valid_reply(context)
        result['scenes'][0]['character_state']['environment_state'] = ''
        return result
    workshop.reply = reply
    with pytest.raises(NumericV2BranchError):
        _draft(workshop)
    assert len(workshop.calls) == 2
    _unchanged(workshop)


def test_revision_change_after_first_candidate_stops_before_second_model(workshop):
    edited = []

    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['transitions'][0]['bridge_scene_narration'] = ''
            edited.append(workshop.sdk.update_project(
                workshop.project['project_id'], base_revision=workshop.project['revision'],
                changes={'title': '作者在等待期间保存的新标题'},
            ))
            # If a stale second model is incorrectly started, let it return so
            # the assertion below distinguishes early fencing from late save refusal.
            workshop.before_bytes = workshop.path.read_bytes()
        return result

    workshop.reply = reply
    with pytest.raises(NumericV2RevisionConflictError):
        _draft(workshop)
    assert len(workshop.calls) == 1
    assert workshop.path.read_bytes() == workshop.before_bytes
    assert workshop.sdk.get_project(workshop.project['project_id']) == edited[0]
    assert set(edited[0]['authoring']['branch_drafts']) == {workshop.ending['draft_id']}


def test_writable_rejection_after_first_candidate_stops_before_second_model(workshop, monkeypatch):
    denied = []

    @contextmanager
    def writable():
        if denied:
            raise PermissionError('injected_writable_denied')
        yield

    def reply(context, attempt):
        result = _valid_reply(context)
        if attempt == 1:
            result['transitions'][0]['bridge_scene_narration'] = ''
            denied.append(True)
        return result

    monkeypatch.setattr(workshop.sdk, '_host_transaction', writable)
    workshop.reply = reply
    with pytest.raises(PermissionError, match='injected_writable_denied'):
        _draft(workshop)
    assert len(workshop.calls) == 1
    _unchanged(workshop)


def _endpoint_context(mode):
    ending = _ending_reply()
    ending['opening_scene'] = '只属于固定结局开场的模板句乙九。'
    ending['fixed_narrations'] = [{
        'id': 'endpoint_original', 'text': ORIGINAL, 'trigger': {'type': 'entry'},
        'after': [], 'required_before_exit': True,
    }]
    ending['must_not_happen'] = ['不得把保留分歧改为双方已经和解。']
    if mode != 'new_ending':
        ending['goals'] = [{
            'id': 'ending_goal', 'owner': goal['owner'], 'description': goal['description'],
            'evidence': {'mode': 'exact', 'anchors': [ORIGINAL]},
            'delivery': {'type': goal['delivery_type'], 'timing': goal['timing'],
                         'state_effects': {'dialogue_policy': 'optional'}},
        } for goal in ending.pop('ordered_goals')]
        ending['acting_contract'] = ending['character_state'].pop('acting_contract')
    return {
        'source': {'id': 'source_node', 'story_beat': {'opening_scene': '当前来源开场保留。'}},
        'author_intent': {'direction': '依次完成必要交付，再进入固定终点。', 'scene_count': 1},
        'endpoint': {
            'mode': mode, 'node_id': 'fixed_endpoint', 'title': '固定终点标题',
            'ending' if mode == 'new_ending' else 'story_beat': ending,
        },
    }


@pytest.mark.parametrize('mode', ['new_ending', 'existing_ending'])
def test_path_sends_ending_requirements_without_the_endpoint_performance_template(mode):
    context = _endpoint_context(mode)
    before = deepcopy(context)
    body = context['endpoint']['ending' if mode == 'new_ending' else 'story_beat']
    captured = []

    def model(messages, **options):
        assert options['operation'] == 'numeric_v2_branch_path'
        captured.append(prompt_payload(messages[1]['content']))
        return ModelReply('{}', 'fixture-model')

    generator = NumericV2Generator(model)
    generator._generate_branch_json(prompt='发送合同回归。', context=context,
                                    max_tokens=6000, operation='numeric_v2_branch_path')
    assert len(captured) == 1 and context == before
    sent = captured[0]
    assert {key: value for key, value in sent.items() if key != 'endpoint'} == {
        key: value for key, value in before.items() if key != 'endpoint'}
    endpoint = sent['endpoint']
    assert {key: endpoint[key] for key in ('mode', 'node_id', 'title')} == {
        key: before['endpoint'][key] for key in ('mode', 'node_id', 'title')}
    assert 'ending' not in endpoint and 'story_beat' not in endpoint
    assert not {'opening_scene', 'goals', 'ordered_goals'}.intersection(endpoint)
    required = endpoint['required_after_path']
    assert not {'opening_scene', 'goals', 'ordered_goals'}.intersection(required)
    assert required['terminal_observation'] == body['opening_scene']
    assert required['terminal_character_context'] == body['catgirl_situation']
    for field in ('summary', 'irreversible_facts', 'tone', 'must_not_happen'):
        assert required[field] == body[field]
    assert required['outcomes'] == [goal['description'] for goal in body.get('ordered_goals', body.get('goals'))]
    assert required['state_constraints'] == body['character_state']
    if 'acting_contract' in body:
        assert required['acting_contract'] == body['acting_contract']
    else:
        assert required['state_constraints']['acting_contract'] == body['character_state']['acting_contract']
    if mode == 'existing_ending':
        assert required['outcome_constraints'] == [{
            'outcome_index': 0, 'anchors': [ORIGINAL], 'state_effects': {'dialogue_policy': 'optional'},
        }]
    assert endpoint['reserved_endpoint_originals'] == body['fixed_narrations']


@pytest.mark.parametrize('mode,operation', [
    ('mainline', 'numeric_v2_branch_path'), ('new_ending', 'numeric_v2_branch_ending'),
])
def test_rejoin_and_ending_generation_keep_their_existing_endpoint_protocol(mode, operation):
    context = _endpoint_context(mode)
    before = deepcopy(context)
    captured = []

    def model(messages, **options):
        assert options['operation'] == operation
        captured.append(prompt_payload(messages[1]['content']))
        return ModelReply('{}', 'fixture-model')

    NumericV2Generator(model)._generate_branch_json(
        prompt='既有发送协议回归。', context=context, max_tokens=6000, operation=operation)
    assert captured == [before]
    assert context == before


@pytest.mark.parametrize('model_name,operation,output_tokens,budget,staged', [
    ('qwen3.8-flash', 'numeric_v2_branch_path', 6000, 2048, False),
    ('qwen3.8-flash', 'numeric_v2_branch_path', 3000, 1500, False),
    ('qwen3.8-flash', 'numeric_v2_fact_review', 6000, None, False),
    ('qwen3.8-max', 'numeric_v2_branch_path', 6000, None, False),
    ('qwen3.8-flash', 'numeric_v2_branch_ending', 6000, None, False),
    ('qwen3.8-flash', 'numeric_v2_story_frame', 6000, 2048, True),
])
def test_host_branch_reasoning_keeps_json_output_and_does_not_change_other_policies(
    monkeypatch, model_name, operation, output_tokens, budget, staged,
):
    from config.providers import MODELS_EXTRA_BODY_MAP
    from utils import llm_client

    messages = [{'role': 'system', 'content': '保留作者合同。'}, {'role': 'user', 'content': '生成有限支线。'}]
    original_messages = deepcopy(messages)
    original_defaults = deepcopy(MODELS_EXTRA_BODY_MAP)
    captured, invocations, closed = [], [], []

    class Client:
        def invoke(self, actual, **options):
            invocations.append((deepcopy(actual), deepcopy(options)))
            return SimpleNamespace(content='{}', response_metadata={})

        def close(self):
            closed.append(True)

    def create(**options):
        captured.append(deepcopy(options))
        return Client()

    monkeypatch.setattr(llm_client, 'create_chat_llm', create)
    model = NekoWorkshopModel({
        'model': model_name, 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'api_key': 'fixture-key', 'max_input_tokens': count_tokens(json.dumps(_with_workshop_watermark(messages), ensure_ascii=False)),
    })
    result = model(messages, max_tokens=output_tokens, max_retries=1,
                   response_format={'type': 'json_object'}, thinking={'type': 'disabled'}, operation=operation)
    assert result.content == '{}'
    assert len(captured) == 1 and closed == [True]
    assert captured[0]['max_completion_tokens'] == output_tokens
    assert captured[0]['max_retries'] == 0
    assert captured[0].get('extra_body') == (
        {'enable_thinking': True, 'thinking_budget': budget} if budget is not None else None)
    assert invocations == [(_with_workshop_watermark(original_messages), {'response_format': None if staged else {'type': 'json_object'}})]
    assert messages == original_messages
    assert MODELS_EXTRA_BODY_MAP == original_defaults

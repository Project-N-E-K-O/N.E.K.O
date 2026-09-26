import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.mini_game_sdk.structured_output import StructuredOutputAttemptsExhausted
from main_logic.watch_together.engine import structured_json_completion


@pytest.mark.asyncio
@pytest.mark.parametrize('content,finish_reason,error', [
    ('', 'stop', 'ValueError'),
    ('{"events": [{"text": "PRIVATE_VIDEO_TEXT",}]}', 'stop', 'JSONDecodeError'),
    ('{"events": [', 'stop', 'JSONDecodeError'),
    # A provider that reports the cut-off never reaches the parser.
    ('{"events": [', 'length', 'ValueError'),
    ('{"events": []}', 'length', 'ValueError'),
    pytest.param('[' * 2000 + '"PRIVATE_VIDEO_TEXT"' + ']' * 2000,
                 'stop', 'RecursionError', id='excessive-nesting'),
])
async def test_failed_attempts_preserve_structure_without_response_text(
    monkeypatch, content, finish_reason, error,
):
    clients = []

    async def factory(**kwargs):
        client = SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(
                content=content, response_metadata={'finish_reason': finish_reason},
            )),
            aclose=AsyncMock(),
        )
        clients.append(client)
        return client

    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    job = {}
    with pytest.raises(StructuredOutputAttemptsExhausted):
        await structured_json_completion(
            {'model': 'test-model'}, 'Return JSON.', [], job,
            lambda value: (value, []), stage='analyzing', label='timeline',
        )
    diagnostics = job['structured_output_failures']
    assert [item['attempt'] for item in diagnostics] == [1, 2]
    assert all(item['parse_error'] == error for item in diagnostics)
    assert all(item['finish_reason'] == finish_reason for item in diagnostics)
    assert all(item['content_length'] == len(content) for item in diagnostics)
    assert 'PRIVATE_VIDEO_TEXT' not in json.dumps(job)
    assert len(clients) == 2
    for client in clients:
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [0, 1, 3])
@pytest.mark.parametrize('shape', ['array', 'object', 'fenced_array'])
async def test_timeline_preserves_every_event_without_retry(tmp_path, monkeypatch, count, shape):
    from main_logic.watch_together.engine import Engine

    events = [{'at': i * 5, 'evidence_at': i * 5, 'kind': 'comment',
               'text': 'A ball moved.', 'reason': 'Visible motion.', 'confidence': .9}
              for i in range(count)]
    content = json.dumps({'events': events} if shape == 'object' else events)
    if shape == 'fenced_array':
        content = '```json\n' + content + '\n```'
    client = SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content=content, response_metadata={})),
        aclose=AsyncMock(),
    )
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    instance = Engine(tmp_path, AsyncMock(), 'cat')
    monkeypatch.setattr(instance, 'vision_config', AsyncMock(return_value={'model': 'test'}))
    result = await instance.llm([], {})
    assert result == {'events': events}
    factory.assert_awaited_once()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('root_shape', ['array', 'object'])
@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('recovers', [False, True])
async def test_timeline_wrappers_in_event_arrays_retry_instead_of_losing_events(
    tmp_path, monkeypatch, mixed, recovers, root_shape,
):
    from main_logic.watch_together.engine import Engine

    events = [{'at': 5, 'evidence_at': 5, 'kind': 'comment',
               'text': 'A ball moved.', 'reason': 'Visible motion.', 'confidence': .9}]
    wrapped = [{'events': events}]
    if mixed:
        wrapped = events + wrapped
    if root_shape == 'object':
        wrapped = {'events': wrapped}
    responses = (wrapped, events if recovers else wrapped)
    clients = [SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(
            content=json.dumps(value), response_metadata={'finish_reason': 'stop'},
        )),
        aclose=AsyncMock(),
    ) for value in responses]
    factory = AsyncMock(side_effect=clients)
    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    instance = Engine(tmp_path, AsyncMock(), 'cat')
    monkeypatch.setattr(instance, 'vision_config', AsyncMock(return_value={'model': 'test'}))
    if recovers:
        assert await instance.llm([], {}) == {'events': events}
    else:
        with pytest.raises(ValueError, match='Invalid timeline response'):
            await instance.llm([], {})
    assert factory.await_count == 2
    for client in clients:
        client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason', ['length', 'stop', None])
@pytest.mark.parametrize('content', [
    # The example passes the schema, but the actual answer is incomplete.
    'Format: {"line": "example"}\nAnswer: {"line":',
    'Format: {"line": "example"}\nAnswer: {"line": "actu',
])
async def test_a_cut_off_reply_does_not_speak_the_example_ahead_of_it(
    monkeypatch, content, finish_reason,
):
    from main_logic.watch_together.live import _validator

    async def factory(**kwargs):
        return SimpleNamespace(
            ainvoke=AsyncMock(return_value=SimpleNamespace(
                content=content, response_metadata={'finish_reason': finish_reason},
            )),
            aclose=AsyncMock(),
        )

    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    job = {}
    with pytest.raises(StructuredOutputAttemptsExhausted):
        await structured_json_completion(
            {'model': 'test-model'}, 'Return JSON.', [], job,
            _validator('interject'), stage='live', label='live_interject',
        )
    assert [item['attempt'] for item in job['structured_output_failures']] == [1, 2]
    assert all(item['finish_reason'] == finish_reason for item in job['structured_output_failures'])
    assert 'example' not in json.dumps(job)


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', [
    'Format: {"events": []}\nAnswer: {"events": [',
    'Format: []\nAnswer: [{"text": "PRIVATE_VIDEO_TEXT',
    'Format: []\nAnswer: [[{"text": "PRIVATE_VIDEO_TEXT',
    pytest.param('[' * 2000 + '"PRIVATE_VIDEO_TEXT"' + ']' * 2000, id='excessive-nesting'),
])
async def test_invalid_timeline_retries_with_a_fresh_client_and_keeps_the_answer(
    tmp_path, monkeypatch, broken,
):
    from main_logic.watch_together.engine import Engine

    events = [{'at': 5, 'text': 'Actual answer'}]
    clients = [SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(
            content=content, response_metadata={'finish_reason': 'stop'},
            usage={'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
        )),
        aclose=AsyncMock(),
    ) for content in (broken, json.dumps(events))]
    factory = AsyncMock(side_effect=clients)
    monkeypatch.setattr('utils.llm_client.create_chat_llm_async', factory)
    instance = Engine(tmp_path, AsyncMock(), 'cat')
    monkeypatch.setattr(instance, 'vision_config', AsyncMock(return_value={'model': 'test'}))
    job = {}

    assert await instance.llm([], job) == {'events': events}
    assert factory.await_count == 2
    assert len(job['structured_output_failures']) == 1
    assert job['usage']['total_tokens'] == 60
    assert 'PRIVATE_VIDEO_TEXT' not in json.dumps(job)
    for client in clients:
        client.aclose.assert_awaited_once()


def test_malformed_array_is_not_salvaged_as_its_nested_object():
    from main_logic.watch_together.engine import json_object

    with pytest.raises(json.JSONDecodeError):
        json_object('[{"events": []}')


def _events_validator(value):
    if isinstance(value, list) and all(isinstance(event, dict) for event in value):
        value = {'events': value}
    valid = isinstance(value, dict) and isinstance(value.get('events'), list)
    return value, [] if valid else [{'field': 'events', 'reason': 'expected_array'}]


@pytest.mark.parametrize('text,expected', [
    # Providers we do not send response_format to append prose after the root.
    ('{"events": []}\nDone. Hope this helps.', {'events': []}),
    ('[{"kind": "laugh"}]\n以上是分析结果。', [{'kind': 'laugh'}]),
    # A bracketed label that cannot decode at all is prose.
    ('Result [JSON]:\n{"events": []}', {'events': []}),
    ('分析 [timeline] 如下：\n[{"kind": "comment"}] 完毕', [{'kind': 'comment'}]),
    # A bracketed label that breaks partway through is still prose: the decoder
    # read the 1 before failing, but text remained after the break.
    ('Result [1 of 1]:\n{"events": []}', {'events': []}),
])
def test_prose_around_the_root_is_ignored(text, expected):
    from main_logic.watch_together.engine import json_object

    assert json_object(text, _events_validator) == expected


@pytest.mark.parametrize('prefix', [
    'Format: {"events": [...]}',
    'Step [1] Format: {"events": [...]}',
    'Format: [{"text": ...}]',
    'Format: {"events": [...], "nested": {"events": []}}',
    'Format: {"events": [...], "note": ' + json.dumps('brace } and escaped quote "') + '}',
])
def test_a_malformed_format_example_does_not_hide_the_later_answer(prefix):
    from main_logic.watch_together.engine import json_object

    expected = {'events': [{'text': 'actual'}]}
    assert json_object(prefix + '\nAnswer: ' + json.dumps(expected), _events_validator) == expected


@pytest.mark.parametrize('text', [
    # A nested complete value is not a later independent answer.
    '{"events": [...], "nested": {"events": []}}',
    # A complete but malformed final answer must not hand back the example.
    'Format: {"events": []}\nAnswer: {"events": [...]}',
    'Format: {"events": []}\nAnswer: {"events": [...]}\nStep [1]',
    'Format: {"events": []}\nAnswer: {"events": [...]}\n{"other": true}',
    # An unclosed or mismatched container does not establish a sibling boundary.
    '{"events": [...], "nested": {"events": []}',
    '{"events": [...] ]\n{"events": []}',
])
def test_a_malformed_object_requires_a_later_independent_valid_answer(text):
    from main_logic.watch_together.engine import json_object

    with pytest.raises(json.JSONDecodeError):
        json_object(text, _events_validator)


@pytest.mark.parametrize('text,expected', [
    # A label such as "Step [1]:" is itself valid JSON, so only the schema can
    # say it is not the payload.
    ('Step [1]: {"events": [{"kind": "laugh"}]}', {'events': [{'kind': 'laugh'}]}),
    ('[2] 结果：\n[{"kind": "comment"}]', [{'kind': 'comment'}]),
])
def test_decodable_prose_labels_lose_to_the_schema(text, expected):
    from main_logic.watch_together.engine import json_object

    assert json_object(text, _events_validator) == expected


@pytest.mark.parametrize('text', [
    '[{"events": []}',            # cut off at the end of the reply
    '[{"events": []} extra',      # cut off, then a stray token
    '{"events": [{"kind": "laugh"}]',
])
def test_a_cut_off_reply_never_hands_back_its_last_whole_fragment(text):
    from main_logic.watch_together.engine import json_object

    with pytest.raises(json.JSONDecodeError):
        json_object(text, _events_validator)


@pytest.mark.parametrize('text', [
    '{"events": []}\nNote [optional',
    '{"events": []}\nNote {unclosed',
    # The bracket is the last character, so the decoder runs out of input on it.
    '{"events": []}\nNote [',
    '{"events": []}\n参见 {',
])
def test_an_unclosed_bracket_in_trailing_prose_does_not_lose_the_payload(text):
    from main_logic.watch_together.engine import json_object

    # Bare brackets and prose labels do not start a quoted JSON member, so
    # they cannot bury a complete payload ahead of them.
    assert json_object(text, _events_validator) == {'events': []}


def test_a_nested_payload_is_not_offered_when_its_container_decoded():
    from main_logic.watch_together.engine import json_object

    # The wrapper decoded whole, so it is the model's answer and the schema
    # rejecting it must reach the retry path rather than the inner object.
    text = '{"result": {"events": [{"kind": "laugh"}]}}'
    assert json_object(text, _events_validator) == json.loads(text)


@pytest.mark.parametrize('text', ['[oops {"events": []}]', '{oops {"events": []}}'])
def test_complete_reply_wrapped_in_junk_still_yields_its_payload(text):
    from main_logic.watch_together.engine import json_object

    # Deliberate: junk around a complete payload is the same shape as the prose
    # prefixes we must skip ("Result [1 of 1]:"), so refusing here would bring
    # back the retry exhaustion this module exists to fix. The reply is whole,
    # the schema accepts the payload, and nothing is lost by reading it. Only a
    # reply that ran out of input is refused, because there the nested object
    # can be a prefix of an answer the model never finished writing.
    assert json_object(text, _events_validator) == {'events': []}


def test_first_root_is_returned_when_the_schema_accepts_nothing():
    from main_logic.watch_together.engine import json_object

    # The caller reports the issues and retries, as it did before candidates.
    assert json_object('[1] {"other": true}', _events_validator) == [1]


def test_a_live_array_of_replies_is_rejected_whole_not_spoken_first():
    from main_logic.watch_together.engine import json_object
    from main_logic.watch_together.live import _validator

    # Yielding the first nested object would speak one line and drop the rest.
    validate = _validator('interject')
    value = json_object('[{"line": "first"}, {"line": "second"}]', validate)
    assert value == [{'line': 'first'}, {'line': 'second'}]
    assert validate(value)[1]


@pytest.mark.parametrize('text', [
    # A broken wrapper leaves both replies loose; speaking one drops the other.
    '[oops {"line": "first"}, {"line": "second"}]',
    # A schema example ahead of the answer: taking the earliest speaks the sample.
    'Format: {"line": "what to say"}\nAnswer: {"line": "actual"}',
])
def test_two_schema_valid_roots_are_refused_rather_than_guessed(text):
    from main_logic.watch_together.engine import json_object
    from main_logic.watch_together.live import _validator

    with pytest.raises(ValueError):
        json_object(text, _validator('interject'))


def test_live_reply_still_requires_its_own_object_schema():
    from main_logic.watch_together.live import _validator

    _, issues = _validator('interject')([{'line': 'hello'}])
    assert issues

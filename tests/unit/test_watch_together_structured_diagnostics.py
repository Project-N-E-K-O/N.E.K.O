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
    ('{"events": [', 'length', 'JSONDecodeError'),
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


def test_malformed_array_is_not_salvaged_as_its_nested_object():
    from main_logic.watch_together.engine import json_object

    with pytest.raises(json.JSONDecodeError):
        json_object('[{"events": []}')


@pytest.mark.parametrize('text,expected', [
    # Providers we do not send response_format to append prose after the root.
    ('{"events": []}\nDone. Hope this helps.', {'events': []}),
    ('[{"kind": "laugh"}]\n以上是分析结果。', [{'kind': 'laugh'}]),
    # Bracketed labels before the payload must not be mistaken for the root.
    ('Result [JSON]:\n{"events": []}', {'events': []}),
    ('分析 [timeline] 如下：\n[{"kind": "comment"}] 完毕', [{'kind': 'comment'}]),
])
def test_prose_around_the_root_is_ignored(text, expected):
    from main_logic.watch_together.engine import json_object

    assert json_object(text) == expected


def test_live_reply_still_requires_its_own_object_schema():
    from main_logic.watch_together.live import _validator

    _, issues = _validator('interject')([{'line': 'hello'}])
    assert issues

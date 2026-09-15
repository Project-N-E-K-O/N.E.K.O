import asyncio
import time
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from main_logic.core.proactive import ProactiveMixin
from main_logic.proactive_delivery import CALLBACK_EXPIRES_AT_KEY, DELIVERY_ACK_FUTURE_KEY
from main_logic.watch_together import live
from main_routers import watch_together_router as router
from main_routers.game_router import runtime
from main_routers.game_router.route_lifecycle import _TAKEOVER_CALLBACK_INBOX_KEY


def _cue(text, *, priority=0, key=''):
    cue = {'origin': 'event', 'status': 'completed', 'summary': text, 'detail': text,
           'source_kind': 'plugin', 'source_name': 'neko_live', 'priority': priority,
           'coalesce_key': key, 'media_images': []}
    cue[DELIVERY_ACK_FUTURE_KEY] = asyncio.get_running_loop().create_future()
    return cue


def _ack(cue):
    future = cue[DELIVERY_ACK_FUTURE_KEY]
    return future.result() if future.done() else None


@pytest.mark.asyncio
async def test_inbox_orders_coalesces_and_sheds_like_proactive_delivery():
    inbox = live.LiveInbox(limit=3)
    old_gift, new_gift = _cue('old gift', priority=9, key='gift'), _cue('new gift', priority=9, key='gift')
    low, mid, late_low = _cue('low'), _cue('mid', priority=5), _cue('late low')
    accepted = [inbox.accept(cue) for cue in (old_gift, new_gift, low, mid)]
    assert all(accepted)
    assert _ack(old_gift) is False, 'a newer cue with the same coalesce key replaces the held one'
    accepted_late = inbox.accept(late_low)
    assert accepted_late
    assert _ack(late_low) is False, 'over the limit, the cue delivered last is shed'
    assert [cue['summary'] for cue in inbox.take(8)] == ['new gift', 'mid', 'low']
    assert inbox.pending == 0
    assert _ack(new_gift) is None, 'taking a cue leaves its acknowledgement to the speaker'


@pytest.mark.asyncio
async def test_expired_cues_do_not_shed_a_live_one():
    inbox = live.LiveInbox(limit=2)
    expired = [_cue(f'expired {index}', priority=9) for index in range(2)]
    for cue in expired:
        inbox.accept(cue)
        cue[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
    fresh = _cue('fresh')
    accepted = inbox.accept(fresh)
    assert accepted and _ack(fresh) is None
    assert all(_ack(cue) is False for cue in expired)
    assert inbox.take(8) == [fresh]


@pytest.mark.asyncio
@pytest.mark.parametrize('source_kind', ['topic', 'cu', 'browser', 'system', None])
async def test_inbox_leaves_non_plugin_proactive_cues_behind_the_gate(source_kind):
    inbox = live.LiveInbox()
    cue = _cue('ordinary proactive speech')
    cue['source_kind'] = source_kind
    assert not inbox.accept(cue)
    assert inbox.pending == 0 and _ack(cue) is None


@pytest.mark.asyncio
async def test_inbox_drops_expired_and_aged_cues_and_close_hands_back_recent_ones():
    now = [0.0]
    inbox = live.LiveInbox(max_age=300, clock=lambda: now[0])
    aged = _cue('aged')
    inbox.accept(aged)
    now[0] = 301
    expired, older = _cue('expired'), _cue('older')
    expired[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
    inbox.accept(expired)
    inbox.accept(older)
    assert inbox.pending == 1
    assert _ack(aged) is False and _ack(expired) is False
    now[0] = 390
    recent = _cue('recent')
    inbox.accept(recent)
    now[0] = 395
    assert inbox.close() == [recent], 'only cues recent enough for ordinary delivery are handed back'
    assert _ack(older) is False and _ack(recent) is None
    remaining = recent[CALLBACK_EXPIRES_AT_KEY] - time.monotonic()
    assert live.HANDOFF_MAX_AGE_SECONDS - 5 - 2 < remaining <= live.HANDOFF_MAX_AGE_SECONDS - 5, \
        'the handed-back cue keeps its remaining delivery window'
    assert not inbox.accept(_cue('after close'))
    assert inbox.pending == 0


def _submitter(sink=None, *, takeover=True):
    return SimpleNamespace(
        lanlan_name='Lan', _takeover_active=takeover, _takeover_callback_sink=sink,
        is_goodbye_silent=lambda: False, enqueue_agent_callback=Mock(),
        proactive_manager=SimpleNamespace(submit=Mock(return_value=[])),
        _recompute_coalesce_latest=Mock(),
    )


@pytest.mark.parametrize('sink,takeover,queued', [
    (lambda callback: True, True, False),
    (lambda callback: True, False, True),
    (lambda callback: False, True, True),
    (Mock(side_effect=RuntimeError('broken')), True, True),
    (None, True, True),
])
def test_respond_cues_go_to_takeover_sink_only_while_it_accepts(sink, takeover, queued):
    manager = _submitter(sink, takeover=takeover)
    ProactiveMixin.submit_proactive_callback(manager, {'summary': 'gift'}, priority=9)
    assert manager.proactive_manager.submit.called is queued


def test_takeover_sink_sees_the_submitted_priority():
    seen = []
    manager = _submitter(lambda callback: seen.append(dict(callback)) or True)
    ProactiveMixin.submit_proactive_callback(manager, {'summary': 'gift'}, priority=9)
    ProactiveMixin.submit_proactive_callback(manager, {'summary': 'own', 'priority': 3}, priority=9)
    assert [callback['priority'] for callback in seen] == [9, 3]


@pytest.mark.asyncio
async def test_failed_watch_takeover_hands_held_cues_back_to_ordinary_delivery():
    inbox = live.LiveInbox()
    held = _cue('held', priority=8, key='reminder')
    inbox.accept(held)
    manager = SimpleNamespace(
        _takeover_active=True, _takeover_input_dispatcher=object(), _takeover_callback_sink=inbox.accept,
        interrupt_ordinary_speech_for_takeover=AsyncMock(side_effect=RuntimeError()),
    )

    def submit(callback, **kwargs):
        assert manager._takeover_active is False and manager._takeover_callback_sink is None, 'handoff after release'
        submitted.append((callback, kwargs))
    submitted = []
    manager.submit_proactive_callback = submit
    with pytest.raises(RuntimeError):
        await runtime._start_watch_speech_takeover({_TAKEOVER_CALLBACK_INBOX_KEY: inbox}, manager)
    assert submitted == [(held, {'priority': 8, 'coalesce_key': 'reminder'})]
    assert _ack(held) is None
    assert not inbox.accept(_cue('late'))


@pytest.mark.asyncio
async def test_closing_takeover_inbox_without_a_session_releases_cues():
    from main_routers.game_router.route_lifecycle import _close_takeover_callback_inbox
    inbox = live.LiveInbox()
    held = _cue('held')
    inbox.accept(held)
    _close_takeover_callback_inbox({_TAKEOVER_CALLBACK_INBOX_KEY: inbox}, None)
    assert _ack(held) is False
    _close_takeover_callback_inbox({}, None)


@pytest.fixture
def scene(monkeypatch):
    from main_routers import shared_state
    from utils import game_route_state
    inbox = live.LiveInbox()
    state = {'game_route_active': True, 'session_id': 's', _TAKEOVER_CALLBACK_INBOX_KEY: inbox}
    monkeypatch.setattr(game_route_state, '_get_active_game_route_state',
                        lambda name, game: state if (name, game) == ('Lan', 'watch-together') else None)
    monkeypatch.setattr(runtime, '_sdk_route_instance_error', lambda _state, _data: None)
    manager = SimpleNamespace(user_language='en', _user_language_explicit=True)
    monkeypatch.setattr(shared_state, 'get_session_manager', lambda: {'Lan': manager})
    monkeypatch.setattr(router, 'application_library', lambda: 'library')
    monkeypatch.setattr(live, 'video_context', lambda library, job, version: {
        'title': 'Cats', 'duration': 60, 'events': [], 'description': ''})
    compose = AsyncMock(return_value=['hi'])
    speak = AsyncMock(return_value=[{'text': 'hi', 'audio': f'{live.AUDIO_ROUTE}/token', 'duration': 1.0}])
    monkeypatch.setattr(live, 'compose', compose)
    monkeypatch.setattr(live, 'speak', speak)
    return SimpleNamespace(state=state, inbox=inbox, manager=manager, compose=compose, speak=speak)


def _request(**body):
    payload = {'lanlan_name': 'Lan', 'session_id': 's', 'job': 'job', 'version': 'v', **body}
    return SimpleNamespace(headers={}, json=AsyncMock(return_value=payload))


@pytest.mark.asyncio
async def test_interject_without_held_cues_skips_generation(scene):
    assert await router.live(_request(action='interject', position=10, gap=20)) == {'lines': []}
    scene.compose.assert_not_awaited()


@pytest.mark.asyncio
async def test_interject_speaks_held_cues_within_the_gap(scene):
    gift = _cue('gift', priority=9)
    scene.inbox.accept(gift)
    result = await router.live(_request(action='interject', position=10, gap=6))
    assert result['lines'][0]['text'] == 'hi'
    kwargs = scene.compose.await_args.kwargs
    assert kwargs['mode'] == 'interject' and kwargs['callbacks'] == [gift]
    assert kwargs['position'] == 10 and kwargs['seconds'] == 5
    assert _ack(gift) is True
    assert scene.state[router._LIVE_BUSY_KEY] is False


@pytest.mark.asyncio
async def test_gap_line_is_dropped_when_every_cue_expired_during_generation(scene):
    danmaku = _cue('danmaku reply')
    danmaku[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() + 60
    scene.inbox.accept(danmaku)

    async def slow_generation(*_args, **_kwargs):
        danmaku[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
        return ['late']
    scene.compose.side_effect = slow_generation
    assert await router.live(_request(action='interject', position=10, gap=20)) == {'lines': []}
    assert _ack(danmaku) is False
    kept = _cue('no deadline')
    scene.inbox.accept(kept)
    scene.compose.side_effect = None
    assert (await router.live(_request(action='interject', position=10, gap=20)))['lines']
    assert _ack(kept) is True


@pytest.mark.asyncio
async def test_intermission_runs_without_held_cues(scene):
    result = await router.live(_request(action='intermission'))
    assert result['lines'][0]['text'] == 'hi'
    assert scene.compose.await_args.kwargs['callbacks'] == []


@pytest.mark.asyncio
async def test_generation_failure_and_route_end_release_cues(scene):
    failed = _cue('failed')
    scene.inbox.accept(failed)
    scene.compose.side_effect = RuntimeError('model offline')
    assert await router.live(_request(action='intermission')) == {'lines': [], 'error': 'RuntimeError'}
    assert _ack(failed) is False
    assert scene.state[router._LIVE_BUSY_KEY] is False
    ended = _cue('ended')
    scene.inbox.accept(ended)
    async def end_route(*_args, **_kwargs):
        scene.state['game_route_active'] = False
        return ['late']
    scene.compose.side_effect = end_route
    assert await router.live(_request(action='intermission')) == {'lines': []}
    assert _ack(ended) is False


@pytest.mark.asyncio
async def test_busy_scene_keeps_cues_and_invalid_requests_are_rejected(scene):
    held = _cue('held')
    scene.inbox.accept(held)
    scene.state[router._LIVE_BUSY_KEY] = True
    assert await router.live(_request(action='interject', position=1, gap=9)) == {'lines': [], 'busy': True}
    assert scene.inbox.pending == 1
    scene.state[router._LIVE_BUSY_KEY] = False
    for body in ({'action': 'shout'}, {'action': 'interject', 'position': True, 'gap': 9},
                 {'action': 'interject', 'position': 1, 'gap': float('nan')}, {'action': 'intermission', 'job': ''}):
        with pytest.raises(HTTPException) as caught:
            await router.live(_request(**body))
        assert caught.value.status_code == 400
    with pytest.raises(HTTPException) as caught:
        await router.live(_request(action='intermission', session_id='other'))
    assert caught.value.status_code == 409


@pytest.fixture
def model(monkeypatch):
    from main_logic.watch_together import engine
    from utils import tokenize
    monkeypatch.setattr(tokenize, '_get_encoder', lambda _: None)
    captured = {}
    reply = {}

    async def completion(cfg, system, content, job, validate, **kwargs):
        value, errors = validate(reply['value'])
        assert errors == []
        captured.update(system=system, content=content, kwargs=kwargs)
        return value
    monkeypatch.setattr(engine, 'structured_json_completion', completion)
    monkeypatch.setattr(engine, 'vision_model_config', AsyncMock(return_value={'model': 'vision'}))
    return SimpleNamespace(captured=captured, reply=reply)


@pytest.mark.asyncio
async def test_compose_interject_wraps_untrusted_context_once(model):
    model.reply['value'] = {'line': '  好耶\n 喵 '}
    manager = SimpleNamespace(lanlan_name='Lan', master_name='Master', lanlan_prompt='persona {title}')
    cue = _cue('viewer asks {messages}')
    cue['media_images'] = ['abc', 'def', 'ghi']
    video = {'title': 'Cats {persona}', 'duration': 60, 'description': '',
             'events': [{'at': 3, 'text': 'cute'}, {'at': 40, 'text': 'future spoiler'}]}
    lines = await live.compose(manager, mode='interject', callbacks=[cue], video=video,
                               language='zh-CN', position=10, seconds=4, cm=object())
    assert lines == ['好耶 喵']
    text = model.captured['content'][0]['text']
    assert 'Cats {persona}' in text and 'viewer asks {messages}' in text
    assert '3s cute' in text and 'future spoiler' not in text
    assert '======以下为刚收到的插件消息======' in text and '4秒' in text
    assert 'persona {title}' in model.captured['system'] and 'Master' in model.captured['system']
    assert sum(block['type'] == 'image_url' for block in model.captured['content']) == live.MAX_IMAGES


@pytest.mark.asyncio
async def test_compose_keeps_images_within_the_turn_byte_budget(model):
    from main_logic.proactive_delivery import TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES
    model.reply['value'] = {'line': 'hi'}
    manager = SimpleNamespace(lanlan_name='Lan', master_name='Master', lanlan_prompt='')
    large = 'A' * (TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES * 3 // 4 // 3 * 4)
    callbacks = []
    for text in ('first', 'second'):
        cue = _cue(text)
        cue['media_images'] = [large]
        callbacks.append(cue)
    await live.compose(manager, mode='interject', callbacks=callbacks, language='en', cm=object(),
                       video={'title': 'Cats', 'duration': 60, 'description': '', 'events': []})
    assert sum(block['type'] == 'image_url' for block in model.captured['content']) == 1


@pytest.mark.asyncio
async def test_compose_intermission_returns_summary_then_bounded_replies(model):
    model.reply['value'] = {'summary': 'Fun one.', 'replies': ['a', ' ', 'b', 'c', 'd']}
    manager = SimpleNamespace(lanlan_name='Lan', master_name='Master', lanlan_prompt='')
    video = {'title': 'Cats', 'duration': 60, 'description': 'desc', 'events': [{'at': 50, 'text': 'late'}]}
    lines = await live.compose(manager, mode='intermission', callbacks=[], video=video, language='en', cm=object())
    assert lines == ['Fun one.', 'a', 'b']
    text = model.captured['content'][0]['text']
    assert '50s late' in text and 'desc' in text and 'None' in text


def test_live_prompts_cover_every_locale_with_paired_watermarks():
    from config.prompts import prompts_watch_together as prompts
    locales = {'zh', 'zh-TW', 'en', 'ja', 'ko', 'es', 'pt', 'ru'}
    required = {
        'WATCH_LIVE_SYSTEM_PROMPT': {'{character}', '{master}', '{persona}'},
        'WATCH_LIVE_INTERJECT_PROMPT': {'{character}', '{title}', '{position}', '{duration}', '{reactions}', '{messages}', '{seconds}'},
        'WATCH_LIVE_INTERMISSION_PROMPT': {'{character}', '{title}', '{duration}', '{description}', '{reactions}', '{messages}'},
    }
    for name, placeholders in required.items():
        table = getattr(prompts, name)
        assert set(table) == locales, name
        for locale, text in table.items():
            assert all(placeholder in text for placeholder in placeholders), (name, locale)
            marks = [line for line in text.splitlines() if line.startswith('======')]
            assert marks and len(marks) % 2 == 0, (name, locale)
            for below, above in zip(marks[::2], marks[1::2]):
                assert below.startswith('======以下为') and above.startswith('======以上为'), (name, locale)
                assert below[len('======以下为'):] == above[len('======以上为'):], (name, locale)
                assert not any(mark in below for mark in ':：—'), (name, locale)
    assert set(prompts.WATCH_LIVE_EMPTY_TEXT) == locales
    for language, key in (('zh-CN', 'zh'), ('zh-TW', 'zh-TW'), ('en', 'en'), ('ja', 'ja'),
                          ('ko', 'ko'), ('es', 'es'), ('pt', 'pt'), ('ru', 'ru')):
        assert prompts.normalize_watch_live_locale(language) == key


def test_audio_store_is_token_checked_expiring_and_bounded(monkeypatch):
    monkeypatch.setattr(live, '_audio_store', OrderedDict())
    now = [0.0]
    clock = lambda: now[0]
    token = live.store_audio(b'a', clock=clock)
    assert live.read_audio(token, clock=clock) == b'a'
    assert live.read_audio('../../secret', clock=clock) is None
    now[0] = live.AUDIO_TTL_SECONDS + 1
    assert live.read_audio(token, clock=clock) is None
    tokens = [live.store_audio(b'x', clock=clock) for _ in range(live.AUDIO_STORE_LIMIT + 2)]
    assert live.read_audio(tokens[0], clock=clock) is None
    assert live.read_audio(tokens[-1], clock=clock) == b'x'


@pytest.mark.asyncio
async def test_synthesize_and_speak_serve_official_tts_wav(monkeypatch):
    from main_logic.core import game_speech_audio_cache
    monkeypatch.setattr(live, '_audio_store', OrderedDict())
    pcm = b'\x00\x00' * 4800
    monkeypatch.setattr(game_speech_audio_cache.GAME_SPEECH_AUDIO_CACHE, 'get', lambda key: [pcm] if key == 'key' else None)
    manager = SimpleNamespace(game_speech_audio_cache_identity=lambda text, render_language: ('key', 'voice'),
                              preload_game_speech_audio=AsyncMock(return_value={'ok': True}))
    data, duration = await live.synthesize(manager, 'hello', 'en')
    assert data.startswith(b'RIFF') and duration == pytest.approx(0.1)
    manager.preload_game_speech_audio.assert_awaited_once_with(['hello'], render_language='en')
    voices = iter(['voice', 'new voice'])
    changing = SimpleNamespace(game_speech_audio_cache_identity=lambda text, render_language: ('key', next(voices)),
                               preload_game_speech_audio=AsyncMock(return_value={'ok': True}))
    with pytest.raises(ValueError, match='voice changed'):
        await live.synthesize(changing, 'hello', 'en')
    manager.preload_game_speech_audio.return_value = {'ok': False}
    with pytest.raises(ValueError):
        await live.synthesize(manager, 'hello', 'en')

    async def synthesize(_manager, text, _language):
        if text == 'bad':
            raise ValueError()
        return data, 0.1
    monkeypatch.setattr(live, 'synthesize', synthesize)
    spoken = await live.speak(manager, ['bad', 'good'], 'en')
    assert [line['text'] for line in spoken] == ['good']
    assert live.read_audio(spoken[0]['audio'].rsplit('/', 1)[1]) == data
    with pytest.raises(ValueError):
        await live.speak(manager, ['bad'], 'en')
    assert await live.speak(manager, [], 'en') == []


def test_video_context_reads_bounded_timeline_and_optional_evidence(tmp_path):
    evidence = tmp_path / 'evidence.json'
    evidence.write_text('{"description": "a cat video"}', encoding='utf-8')
    timeline = {'title': 'Cats', 'duration': 61.5,
                'events': [{'at': 3, 'text': 'cute'}, {'at': True, 'text': 'bool'}, {'at': 'x'}, 'bad']}
    library = SimpleNamespace(timeline=lambda job, version: timeline, resource=lambda job, version, name: evidence)
    context = live.video_context(library, 'job', 'v')
    assert context == {'title': 'Cats', 'duration': 61.5, 'events': [{'at': 3.0, 'text': 'cute'}],
                       'description': 'a cat video'}
    def missing(*_args):
        raise KeyError('evidence.json')
    library.resource = missing
    assert live.video_context(library, 'job', 'v')['description'] == ''


@pytest.mark.asyncio
async def test_live_audio_endpoint_serves_only_stored_tokens(monkeypatch):
    monkeypatch.setattr(live, '_audio_store', OrderedDict())
    token = live.store_audio(b'RIFFdata')
    response = await router.live_audio(token)
    assert response.body == b'RIFFdata' and response.media_type == 'audio/wav'
    assert response.headers['cache-control'] == 'no-store'
    with pytest.raises(HTTPException) as caught:
        await router.live_audio('missing-token-000000')
    assert caught.value.status_code == 404

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['oversized', 'provider'])
async def test_synthesis_skips_only_oversized_cues(tmp_path, monkeypatch, failure):
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from main_logic.watch_together import engine

    video = SimpleNamespace(
        get_info=AsyncMock(return_value={'title': 'Video', 'pages': [{'duration': 60, 'cid': 1}], 'stat': {'danmaku': 101}}),
        get_subtitle=AsyncMock(return_value={'subtitles': []}),
        get_danmakus=AsyncMock(return_value=[]),
        get_download_url=AsyncMock(return_value={'durl': [{'url': 'https://example.com/video'}]}),
    )
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(Credential=lambda: None, video=SimpleNamespace(Video=lambda **kw: video)))
    monkeypatch.setattr('utils.web_scraper.platform_helpers._get_bilibili_credential', lambda: None)
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    async def download(client, stream, path, **kwargs):
        path.write_bytes(b'video')
    monkeypatch.setattr(engine, 'download_stream', download)
    monkeypatch.setattr(engine, 'run_media_async', AsyncMock())
    monkeypatch.setattr(engine, 'duration_async', AsyncMock(side_effect=lambda path: 60 if path.name == 'video.mp4' else 1))
    async def synthesize(text, path):
        if text != 'short':
            if failure == 'oversized':
                raise engine.SpeechCueTooLarge()
            raise RuntimeError('provider unavailable')
        path.write_bytes(b'audio')
    instance = engine.Engine(tmp_path, synthesize, 'cat')
    monkeypatch.setattr(instance, 'vision_config', AsyncMock())
    monkeypatch.setattr(instance, 'llm', AsyncMock(return_value={'events': []}))
    monkeypatch.setattr(engine, 'normalize_events', lambda *args: [
        {'at': 5, 'kind': 'comment', 'text': 'long'},
        {'at': 15, 'kind': 'laugh', 'text': ''},
        {'at': 25, 'kind': 'comment', 'text': 'short'},
    ])
    job = {'id': 'job'}
    if failure == 'provider':
        with pytest.raises(RuntimeError, match='provider unavailable'):
            await instance.prepare(job, 'BV1GJ411x7h7', 'cat')
        return
    await instance.prepare(job, 'BV1GJ411x7h7', 'cat')
    assert job['status'] == 'ready'
    assert [cue['text'] for cue in job['events']] == ['short']
    assert job['skipped_cues'] == [{'index': 0, 'reason': 'audio_too_large'}, {'index': 1, 'reason': 'audio_too_large'}]
    assert (tmp_path / 'job' / 'planning.json').exists()


def _budget_engine(tmp_path, monkeypatch, events):
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from main_logic.watch_together import engine

    video = SimpleNamespace(
        get_info=AsyncMock(return_value={'title': 'Video', 'pages': [{'duration': 60, 'cid': 1}], 'stat': {'danmaku': 101}}),
        get_subtitle=AsyncMock(return_value={'subtitles': []}),
        get_danmakus=AsyncMock(return_value=[]),
        get_download_url=AsyncMock(return_value={'durl': [{'url': 'https://example.com/video'}]}),
    )
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(Credential=lambda: None, video=SimpleNamespace(Video=lambda **kw: video)))
    monkeypatch.setattr('utils.web_scraper.platform_helpers._get_bilibili_credential', lambda: None)
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    async def download(client, stream, path, **kwargs):
        path.write_bytes(b'video')
    monkeypatch.setattr(engine, 'download_stream', download)
    monkeypatch.setattr(engine, 'run_media_async', AsyncMock())
    monkeypatch.setattr(engine, 'duration_async', AsyncMock(side_effect=lambda path: 60 if path.name == 'video.mp4' else 1))
    synthesized = []
    async def synthesize(text, path):
        synthesized.append(text)
        path.write_bytes(b'wave')
    instance = engine.Engine(tmp_path, synthesize, 'cat')
    monkeypatch.setattr(instance, 'vision_config', AsyncMock())
    monkeypatch.setattr(instance, 'llm', AsyncMock(return_value={'events': []}))
    monkeypatch.setattr(engine, 'normalize_events', lambda *args: [dict(event) for event in events])
    return engine, instance, synthesized


@pytest.mark.asyncio
async def test_synthesis_byte_budget_counts_shared_laugh_once(tmp_path, monkeypatch):
    engine, instance, _ = _budget_engine(tmp_path, monkeypatch, [
        {'at': 5, 'kind': 'comment', 'text': 'first'},
        {'at': 15, 'kind': 'laugh', 'text': ''},
        {'at': 25, 'kind': 'comment', 'text': 'over'},
        {'at': 35, 'kind': 'laugh', 'text': ''},
    ])
    monkeypatch.setattr(engine, 'MAX_REACTION_AUDIO_BYTES', 10)
    job = {'id': 'job'}
    await instance.prepare(job, 'BV1GJ411x7h7', 'cat')
    assert job['status'] == 'ready'
    assert [(cue['at'], cue['audio'].rsplit('/', 1)[1]) for cue in job['events']] == [
        (5, 'comment-0.wav'), (15, 'laugh.wav'), (35, 'laugh.wav')]
    assert job['skipped_cues'] == [{'index': 2, 'reason': 'audio_budget_exceeded'}]
    # Staging is imported wholesale; rejected audio must not be persisted.
    assert sorted(path.name for path in (tmp_path / 'job').glob('*.wav')) == ['comment-0.wav', 'laugh.wav']


@pytest.mark.asyncio
async def test_synthesis_file_budget_skips_before_synthesizing(tmp_path, monkeypatch):
    engine, instance, synthesized = _budget_engine(tmp_path, monkeypatch, [
        {'at': 5, 'kind': 'comment', 'text': 'first'},
        {'at': 15, 'kind': 'laugh', 'text': ''},
        {'at': 25, 'kind': 'comment', 'text': 'late'},
    ])
    monkeypatch.setattr(engine, 'MAX_REACTION_AUDIO_FILES', 1)
    job = {'id': 'job'}
    await instance.prepare(job, 'BV1GJ411x7h7', 'cat')
    assert job['status'] == 'ready'
    assert [cue['audio'].rsplit('/', 1)[1] for cue in job['events']] == ['comment-0.wav']
    assert job['skipped_cues'] == [{'index': 1, 'reason': 'audio_budget_exceeded'},
                                   {'index': 2, 'reason': 'audio_budget_exceeded'}]
    assert 'late' not in synthesized
    assert sorted(path.name for path in (tmp_path / 'job').glob('*.wav')) == ['comment-0.wav']


@pytest.mark.asyncio
@pytest.mark.parametrize('model', [None, '', '   ', 123])
async def test_missing_vision_model_blocks_preflight_and_invitation(tmp_path, monkeypatch, model):
    from types import SimpleNamespace
    from main_logic.watch_together import engine
    from main_logic.watch_together import preparation
    monkeypatch.setattr(engine, 'media_binary', lambda name: name)
    config = SimpleNamespace(get_model_api_config=lambda _: {'api_key': 'key', 'model': model})
    instance = engine.Engine(tmp_path, None, 'cat')
    instance._cm = config
    with pytest.raises(RuntimeError):
        await instance.vision_config()
    assert not preparation.is_available(SimpleNamespace(_config_manager=config))


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
async def test_vision_uses_provider_factory_and_accepts_keyless_custom(tmp_path, monkeypatch, provider):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from main_logic.watch_together.engine import Engine
    from utils import llm_client
    from utils import tokenize
    monkeypatch.setattr(tokenize, '_get_encoder', lambda _: None)
    response = SimpleNamespace(content='{"events":[]}', response_metadata={'token_usage': {'input_tokens': 10, 'output_tokens': 2}})
    client = SimpleNamespace(ainvoke=AsyncMock(return_value=response), aclose=AsyncMock())
    factory = AsyncMock(return_value=client)
    monkeypatch.setattr(llm_client, 'create_chat_llm_async', factory)
    instance = Engine(tmp_path, None, 'cat')
    instance._cm = SimpleNamespace(get_model_api_config=lambda _: {'api_key': '', 'is_custom': True,
        'model': 'local', 'base_url': 'http://localhost:8000', 'provider_type': provider})
    job = {}
    blocks = [{'type': 'text', 'text': 'evidence ' * 30000}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,abc'}}]
    assert await instance.llm(blocks, job) == {'events': []}
    from utils.tokenize import count_tokens
    sent = client.ainvoke.call_args.args[0]
    assert count_tokens(sent[1]['content'][0]['text']) <= 16000
    assert sent[1]['content'][1] == blocks[1]
    assert sent[0]['content'] == instance.director_prompt
    assert factory.call_args.kwargs['provider_type'] == provider
    assert factory.call_args.kwargs['api_key'] == ''
    assert ('response_format' in client.ainvoke.call_args.kwargs) is (provider == 'openai')
    assert job['usage']['total_tokens'] == 12
    client.aclose.assert_awaited_once()
from main_logic.watch_together.engine import Engine
from main_logic.watch_together.engine import subtitle_priority, dash_audio, media_binary


@pytest.mark.parametrize('language', ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'es', 'pt', 'ru'])
def test_director_uses_current_persona_and_requested_language(tmp_path, language):
    persona = 'Gentle, playful; call the user {captain}. ' * 200
    engine = Engine(tmp_path, None, 'Yui', language=language, persona=persona)
    assert persona in engine.director_prompt
    assert 'Speak as Yui' in engine.director_prompt
    assert f'in {language}.' in engine.director_prompt
    assert engine.director_prompt.endswith('======以上为陪看规则======')


def test_subtitle_script_and_generated_language_priority():
    tracks = [{"lan": "en"}, {"lan": "zh-Hans"}, {"lan": "zh-Hant"}]
    assert min(tracks, key=lambda t: subtitle_priority(t, "zh-TW"))["lan"] == "zh-Hant"
    assert min(tracks, key=lambda t: subtitle_priority(t, "zh-CN"))["lan"] == "zh-Hans"
    assert subtitle_priority({"lan": "ai-en"}, "en") == 0


def test_dash_silent_and_alternate_audio():
    assert dash_audio({}) is None
    assert dash_audio({"audio": []}) is None
    sound = {"baseUrl": "audio", "bandwidth": 10}
    assert dash_audio({"flac": {"audio": sound}}) == sound
    assert dash_audio({"dolby": {"audio": [sound]}}) == sound


def test_configured_media_binary_and_missing_prerequisite(monkeypatch):
    from main_logic.watch_together import engine
    monkeypatch.setenv("NEKO_FFMPEG_PATH", "C:/media tools/ffmpeg.exe")
    monkeypatch.setattr(engine.shutil, "which", lambda value: value if value == "C:/media tools/ffmpeg.exe" else None)
    assert media_binary("ffmpeg") == "C:/media tools/ffmpeg.exe"
    with pytest.raises(FileNotFoundError, match="NEKO_FFPROBE_PATH"):
        media_binary("ffprobe")
from main_logic.watch_together.engine import normalize_events, parse_video_url, danmaku_hotspots, hotspot_frame_times


@pytest.mark.parametrize("language,laugh", [("en", "Hehe, haha!"), ("ja", "ふふ、あはは！"), ("zh-TW", "捏嘿嘿，哈哈！")])
def test_character_language_selects_laughter_without_changing_legacy_baseline(tmp_path, language, laugh):
    engine = Engine(tmp_path, None, "cat", language=language)
    assert engine.language == language
    assert engine.laugh_text == laugh


def test_links_reject_unrelated_hosts_and_keep_page():
    assert parse_video_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=2") == ("BV1GJ411x7h7", 1)
    for url in ("https://evil.test/video/BV1GJ411x7h7", "http://127.0.0.1/video/BV1GJ411x7h7", "https://www.bilibili.com/video/BV1GJ411x7h7?p=0"):
        with pytest.raises(ValueError):
            parse_video_url(url)


def test_rejects_spoilers_nan_weak_evidence_and_dense_cues():
    def cue(at, **kwargs):
        return {"at": at, "evidence_at": at-1, "kind": "laugh", "reason": "画面反转", "confidence": .8, **kwargs}
    actual = normalize_events([cue(10), cue(11), cue(22, evidence_at=25), cue(float('nan')), cue(30, confidence=.2), cue(40)], 60)
    assert [e["at"] for e in actual] == [10, 40]


@pytest.mark.parametrize('confidence,accepted', [(1, True), (.65, True), (1.01, False), (100, False)])
def test_confidence_range(confidence, accepted):
    event = dict(at=2, evidence_at=1, kind='laugh', reason='visual gag', confidence=confidence)
    assert bool(normalize_events([event], 60)) is accepted


@pytest.mark.parametrize('field', ['at', 'evidence_at', 'confidence'])
@pytest.mark.parametrize('value', [True, False])
def test_boolean_model_fields_are_rejected(field, value):
    event = dict(at=1, evidence_at=0, kind='laugh', reason='visual gag', confidence=1)
    assert normalize_events([{**event, field:value}], 60) == []


@pytest.mark.parametrize('field', ['at', 'evidence_at', 'confidence'])
def test_overflowing_model_cue_does_not_discard_valid_cues(field):
    event = dict(at=2, evidence_at=1, kind='laugh', reason='visual gag', confidence=.8)
    invalid = {**event, field: 10**1000}
    assert normalize_events([invalid, event], 60) == normalize_events([event], 60)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'http', 'empty'])
async def test_subtitles_try_remaining_tracks(failure):
    import httpx
    from main_logic.watch_together.engine import fetch_subtitles
    tracks = [{'lan': 'en', 'subtitle_url': 'https://subs.test/fallback'},
              {'lan': 'zh-Hans', 'subtitle_url': 'https://subs.test/preferred'}]
    if failure == 'missing':
        tracks[1].pop('subtitle_url')
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/preferred':
            return httpx.Response(403 if failure == 'http' else 200, json={'body': []})
        return httpx.Response(200, json={'body': [{'from': 0, 'to': 1, 'content': 'spoken context'}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        body = await fetch_subtitles(client, tracks, 'zh-CN')
    assert body[0]['content'] == 'spoken context'
    assert calls == (['/fallback'] if failure == 'missing' else ['/preferred', '/fallback'])


def test_hotspots_cover_middle_and_deduplicate_spam():
    dm = [{"at": 3, "text": "哈哈"}] * 100 + [{"at": 48, "text": "绷不住了"}, {"at": 49, "text": "还有高手"}, {"at": 81, "text": "坠机了"}]
    spots = danmaku_hotspots(dm, 100)
    assert len(spots) == 3
    assert spots[0]["score"] == 3
    assert spots[1]["at"] == 48.5
    assert 45.5 in hotspot_frame_times(spots, 100)
    assert 49.5 in hotspot_frame_times(spots, 100)


def test_hotspot_frames_stay_inside_video():
    frames = hotspot_frame_times([{"at": .2}, {"at": 9.5}], 10)
    assert all(0 <= t < 9.9 for t in frames)


def test_danmaku_sample_is_bounded_and_time_balanced():
    from types import SimpleNamespace
    from main_logic.watch_together.engine import sample_danmaku
    messages = (SimpleNamespace(dm_time=second, text='x' * 200)
                for second in range(180) for _ in range(1000))
    sampled = sample_danmaku(messages, 180)
    assert len(sampled) == 60 * 12
    assert {int(item['at'] // 3) for item in sampled} == set(range(60))
    assert all(len(item['text']) == 120 for item in sampled)

@pytest.mark.asyncio
async def test_subtitles_skip_invalid_times_and_normalize_numeric_strings():
    import httpx
    from main_logic.watch_together.engine import fetch_subtitles
    rows = [{'from': value, 'to': 2} for value in [None, {}, True, 'bad', 10**1000, float('inf'), -1]]
    rows += [{'from': '1', 'to': '2', 'content': 'valid'}, {'from': 3, 'to': 2}]
    response = type('Response', (), {'raise_for_status': lambda self: None, 'json': lambda self: {'body': rows}})()
    from unittest.mock import AsyncMock
    client = type('Client', (), {'get': AsyncMock(return_value=response)})()
    assert await fetch_subtitles(client, [{'subtitle_url': 'https://subs.test'}], 'en') == [
        {'from': 1.0, 'to': 2.0, 'content': 'valid'}]

@pytest.mark.asyncio
async def test_subtitle_payload_drops_extra_fields_and_bounds_text():
    from main_logic.watch_together.engine import fetch_subtitles
    from unittest.mock import AsyncMock
    rows = [{'from': 0, 'to': 1000, 'content': 'x' * 10000, 'extra': 'y' * 10000}] * 3000
    response = type('Response', (), {'raise_for_status': lambda self: None, 'json': lambda self: {'body': rows}})()
    client = type('Client', (), {'get': AsyncMock(return_value=response)})()
    result = await fetch_subtitles(client, [{'subtitle_url': 'https://subs.test'}], 'en')
    assert sum(len(row['content']) for row in result) <= 24000
    assert all(len(row['content']) <= 240 and set(row) == {'from', 'to', 'content'} for row in result)

@pytest.mark.asyncio
async def test_subtitle_budget_preserves_late_windows_and_skips_invalid_tracks():
    from main_logic.watch_together.engine import fetch_subtitles
    from unittest.mock import AsyncMock
    rows = [{'from': 0, 'to': 1, 'content': 'x' * 240}] * 3000
    rows += [{'from': 1190, 'to': 1195, 'content': 'ending dialogue'}]
    response = type('Response', (), {'raise_for_status': lambda self: None, 'json': lambda self: {'body': rows}})()
    client = type('Client', (), {'get': AsyncMock(return_value=response)})()
    result = await fetch_subtitles(client, [None, 1, 'bad', {'subtitle_url': 'https://subs.test'}], 'en')
    assert result[-1]['content'] == 'ending dialogue'
    assert sum(len(row['content']) for row in result if row['from'] == 0) <= 600
    client.get.assert_awaited_once()

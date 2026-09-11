import pytest
from main_logic.watch_together.engine import Engine
from main_logic.watch_together.engine import subtitle_priority, dash_audio, media_binary


def test_director_uses_current_persona_and_requested_language(tmp_path):
    engine = Engine(tmp_path, None, 'Yui', language='zh-CN', persona='Gentle, playful; call the user captain.')
    assert 'Gentle, playful; call the user captain.' in engine.director_prompt
    assert 'Speak as Yui' in engine.director_prompt
    assert 'in zh-CN' in engine.director_prompt
    assert '======以上为' in engine.director_prompt


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

import asyncio
from types import SimpleNamespace

import pytest

from main_logic.watch_together import preparation


@pytest.fixture(autouse=True)
def media_tools_available(monkeypatch):
    monkeypatch.setattr(preparation, 'media_binary', lambda name: name)


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['ffmpeg', 'ffprobe'])
async def test_missing_media_tool_prevents_speech(tmp_path, monkeypatch, missing):
    probes = []
    def binary(name):
        if name == missing:
            raise FileNotFoundError(name)
        return name
    async def probe(*args, **kwargs):
        probes.append(True)
        return {'ok': True}
    monkeypatch.setattr(preparation, 'media_binary', binary)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=probe,
                              game_speech_audio_cache_identity=lambda *a, **kw: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    assert not probes
    assert preparation.jobs[result['id']]['error'] == 'FileNotFoundError'


async def speech_ready(*args, **kwargs):
    return {'ok': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,changed,expected', [
    ('audio_too_large', False, preparation.SpeechCueTooLarge),
    ('tts_incomplete', False, ValueError),
    ('audio_too_large', True, ValueError),
])
async def test_preparation_distinguishes_oversized_audio_from_voice_failure(tmp_path, monkeypatch, reason, changed, expected):
    from unittest.mock import AsyncMock
    signature = 'voice'
    async def preload(*args, **kwargs):
        nonlocal signature
        if changed:
            signature = 'new-voice'
        return {'ok': False, 'results': [{'index': 0, 'status': 'failed', 'reason': reason}]}
    manager = SimpleNamespace(
        game_speech_audio_cache_identity=lambda *a, **kw: ('key', signature),
        preflight_game_speech_audio=speech_ready, preload_game_speech_audio=preload,
    )
    seen = []
    class Engine:
        vision_config = AsyncMock()
        def __init__(self, root, synthesize, *args, **kwargs):
            self.synthesize = synthesize
        async def prepare(self, job, *args, **kwargs):
            try:
                await self.synthesize('cue', tmp_path / 'cue.wav')
            except ValueError as exc:
                seen.append(type(exc))
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    assert seen == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_download_confirmation_pauses_same_job_and_checks_owner(tmp_path, monkeypatch, accepted):
    analyzed = []

    class Engine:
        async def vision_config(self):
            return {'api_key': 'configured'}

        def __init__(self, root, *_args, **_kwargs):
            self.root = root

        async def prepare(self, job, *_args, confirm_download, **_kwargs):
            (self.root / job['id']).mkdir(parents=True)
            if not await confirm_download('Boundary video', 300.04):
                raise asyncio.CancelledError()
            analyzed.append(job['id'])
            job['status'] = 'ready'

    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path, import_sources=lambda *a, **kw: None))
    monkeypatch.setattr(preparation, 'jobs', {})
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'pending_confirmations', {})
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready, game_speech_audio_cache_identity=lambda *a, **kw: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    tasks = list(preparation.tasks)
    await asyncio.sleep(0)
    identifier = result['id']
    assert preparation.jobs[identifier]['status'] == 'awaiting_confirmation'
    assert not analyzed
    for owner, duration in [(object(), 300.04), (manager, 300)]:
        with pytest.raises(ValueError):
            preparation.confirm_preparation(identifier, owner, accepted, duration)
    preparation.confirm_preparation(identifier, manager, accepted, 300.04)
    with pytest.raises(ValueError):
        preparation.confirm_preparation(identifier, manager, accepted, 300.04)
    await asyncio.gather(*tasks, return_exceptions=True)
    assert analyzed == ([identifier] if accepted else [])
    assert preparation.jobs[identifier]['status'] == ('ready' if accepted else 'cancelled')
    assert not preparation.pending_confirmations


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,explicit,expected', [(None, False, 'ja'), ('zh-CN', False, 'zh-CN'), ('zh-CN', True, 'en')])
async def test_preparation_freezes_render_locale_for_director_and_tts(tmp_path, monkeypatch, requested, explicit, expected):
    from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
    calls = []

    def identity(text, *, render_language):
        calls.append(render_language)
        return "audio-key", "voice"

    async def preload(lines, *, render_language):
        calls.append(render_language)
        return {"ok": True}

    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready, user_language="en", _conversation_render_language="ja", lanlan_prompt='Current persona',
                              _user_language_explicit=explicit,
                              game_speech_audio_cache_identity=identity, preload_game_speech_audio=preload)

    class Engine:
        async def vision_config(self):
            return {'api_key': 'configured'}

        def __init__(self, root, synthesize, character, *, language, persona):
            assert language == expected
            assert persona == 'Current persona'
            self.root, self.synthesize = root, synthesize

        async def prepare(self, job, *_args, **_kwargs):
            folder = self.root / job["id"]
            folder.mkdir(parents=True)
            manager._conversation_render_language = "en"
            await self.synthesize("test", folder / "audio.wav")
            job["status"] = "ready"

    monkeypatch.setattr(GAME_SPEECH_AUDIO_CACHE, "get", lambda key: [b'\x00\x00'])
    monkeypatch.setattr(preparation, "Engine", Engine)
    monkeypatch.setattr(preparation, "application_library", lambda: SimpleNamespace(root=tmp_path, import_sources=lambda *a, **kw: None))
    monkeypatch.setattr(preparation, "jobs", {})
    monkeypatch.setattr(preparation, "tasks", set())
    result = await preparation.prepare("video", manager, "cat", render_language=requested)
    await asyncio.gather(*preparation.tasks)
    assert preparation.jobs[result["id"]]["status"] == "ready"
    assert calls == [expected] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("disk error"), RuntimeError("database locked")])
async def test_import_failure_is_terminal_and_preserves_staging(tmp_path, monkeypatch, failure):
    def fail_import(_sources, **kwargs):
        assert kwargs["only_job"]
        assert kwargs["write_report"] is False
        raise failure

    class Engine:
        async def vision_config(self):
            return {'api_key': 'configured'}

        def __init__(self, root, *_args, **_kwargs):
            self.root = root

        async def prepare(self, job, *_args, **_kwargs):
            (self.root / job["id"]).mkdir(parents=True)
            job.update(status="ready", stage="Ready")

    monkeypatch.setattr(preparation, "Engine", Engine)
    monkeypatch.setattr(preparation, "application_library", lambda: SimpleNamespace(root=tmp_path, import_sources=fail_import))
    monkeypatch.setattr(preparation, "jobs", {})
    monkeypatch.setattr(preparation, "tasks", set())
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready, game_speech_audio_cache_identity=lambda *args, **kwargs: ("key", "voice"))
    result = await preparation.prepare("video", manager, "cat")
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result["id"]]
    assert job["status"] == "error"
    assert job["stage"] == "Saving preparation failed"
    assert job["error"] == type(failure).__name__
    assert job["persistence_complete"] is True
    assert (tmp_path / "preparations" / result["id"] / "timeline.json").exists()


@pytest.mark.asyncio
async def test_ready_job_waits_for_persistence(tmp_path, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()

    class Engine:
        def __init__(self, root, *_args, **_kwargs):
            self.root = root

        async def vision_config(self):
            return {}

        async def prepare(self, job, *_args, **_kwargs):
            (self.root / job["id"]).mkdir(parents=True)
            job["status"] = "ready"

    def import_sources(*_args, **_kwargs):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(preparation, "Engine", Engine)
    monkeypatch.setattr(preparation, "application_library", lambda: SimpleNamespace(root=tmp_path, import_sources=import_sources))
    monkeypatch.setattr(preparation, "jobs", {})
    monkeypatch.setattr(preparation, "tasks", set())
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready, game_speech_audio_cache_identity=lambda *args, **kwargs: ("key", "voice"))
    result = await preparation.prepare("video", manager, "cat")
    try:
        assert await asyncio.to_thread(started.wait, 5)
        job = preparation.jobs[result["id"]]
        assert job["status"] == "ready"
        assert job["persistence_complete"] is False
    finally:
        release.set()
        await asyncio.gather(*preparation.tasks)
    assert job["persistence_complete"] is True

@pytest.mark.asyncio
async def test_library_initialization_yields_and_rechecks_preparation_slot(monkeypatch):
    import threading
    event_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    def library():
        assert threading.get_ident() != event_thread
        started.set()
        assert release.wait(5)
        return SimpleNamespace()
    monkeypatch.setattr(preparation, 'application_library', library)
    monkeypatch.setattr(preparation, 'tasks', set())
    pending = asyncio.create_task(preparation.prepare('video', SimpleNamespace(), 'cat'))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        preparation.tasks.add(object())
    finally:
        release.set()
    with pytest.raises(ValueError, match='already being prepared'):
        await pending

@pytest.mark.asyncio
@pytest.mark.parametrize('disabled,supported', [(True, True), (False, False)])
async def test_speech_preflight_checks_provider_before_cache(disabled, supported):
    from main_logic.core.tts_runtime import TtsRuntimeMixin
    manager = SimpleNamespace(
        _resolve_tts_worker_spec=lambda: (None, '', '', 'provider', disabled, {}),
        _tts_worker_supports_completion=lambda *args: supported,
    )
    result = await TtsRuntimeMixin.preflight_game_speech_audio(manager, 'probe', render_language='zh-CN')
    assert result['ok'] is False


@pytest.mark.asyncio
async def test_unavailable_speech_prevents_video_analysis(tmp_path, monkeypatch):
    analyzed = []
    class Engine:
        async def vision_config(self):
            return {'api_key': 'configured'}

        def __init__(self, *args, **kwargs):
            pass
        async def prepare(self, *args, **kwargs):
            analyzed.append(True)
    async def unavailable(*args, **kwargs):
        return {'ok': False}
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=unavailable,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    assert not analyzed
    assert preparation.jobs[result['id']]['status'] == 'error'


@pytest.mark.asyncio
async def test_missing_vision_prevents_paid_speech_probe(tmp_path, monkeypatch):
    probes = []
    class Engine:
        def __init__(self, *args, **kwargs):
            pass
        async def vision_config(self):
            raise RuntimeError('missing vision key')
    async def probe(*args, **kwargs):
        probes.append(True)
        return {'ok': True}
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=probe,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    assert not probes
    assert preparation.jobs[result['id']]['status'] == 'error'

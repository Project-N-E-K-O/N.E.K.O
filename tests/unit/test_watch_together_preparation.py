import asyncio
from types import SimpleNamespace

import pytest

from main_logic.watch_together import preparation


@pytest.fixture(autouse=True)
def media_codecs_available(monkeypatch):
    monkeypatch.setattr(preparation.media, 'check_available', lambda: None)


@pytest.mark.asyncio
async def test_missing_media_codec_prevents_speech(tmp_path, monkeypatch):
    probes = []
    def binary():
        raise RuntimeError("Required codec unavailable")
    async def probe(*args, **kwargs):
        probes.append(True)
        return {'ok': True}
    monkeypatch.setattr(preparation.media, 'check_available', binary)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=probe,
                              game_speech_audio_cache_identity=lambda *a, **kw: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    assert not probes
    assert preparation.jobs[result['id']]['error'] == 'RuntimeError'


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


def _provider_connection_failure():
    """Synthetic SDK error wrapping a transport error wrapping an OSError."""
    import errno
    import httpx
    import openai
    request = httpx.Request('POST', 'https://user:pw@free.example.test:8443/v1/chat/completions?api_key=sk-secretquery')
    try:
        try:
            try:
                raise ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')
            except ConnectionResetError as reset:
                raise httpx.ReadError(
                    'read failed for https://free.example.test/v1/chat?token=tok-123 '
                    'with Authorization: Bearer abc.def-ghi and sk-live1234567890 '
                    'access_key=AKIA999 payload data:image/jpeg;base64,/9j/AAAA', request=request) from reset
        except httpx.ReadError as read:
            raise openai.APIConnectionError(request=request) from read
    except openai.APIConnectionError as exc:
        return exc


def test_error_cause_chain_keeps_types_and_errno_but_redacts_secrets():
    chain = preparation.error_cause_chain(_provider_connection_failure())
    assert [layer['type'] for layer in chain] == ['APIConnectionError', 'ReadError', 'ConnectionResetError']
    import errno
    assert [layer['errno'] for layer in chain] == [None, None, errno.ECONNRESET]
    text = ' '.join(layer['message'] for layer in chain)
    assert 'https://free.example.test ' in text
    for secret in ('/v1/chat', 'token=tok-123', 'abc.def-ghi', 'sk-live', 'AKIA999', '/9j/AAAA', 'user:pw', 'secretquery'):
        assert secret not in text
    assert 'Authorization: ***' in text and 'sk-***' in text and 'access_key=***' in text and 'data:***' in text


def test_error_cause_chain_is_bounded_and_survives_cycles():
    def nested(depth):
        exc = ValueError('level 0')
        for level in range(1, depth):
            outer = RuntimeError(f'level {level}')
            outer.__cause__ = exc
            exc = outer
        return exc
    assert len(preparation.error_cause_chain(nested(10))) == preparation.MAX_CAUSE_DEPTH == 4
    assert [layer['message'] for layer in preparation.error_cause_chain(nested(2))] == ['level 1', 'level 0']
    first, second = RuntimeError('a'), RuntimeError('b')
    first.__context__, second.__context__ = second, first
    assert len(preparation.error_cause_chain(first)) == 2


def test_error_message_is_truncated_after_redaction():
    # Redacting first lets the text after a long key survive the length cap.
    assert preparation.redact_error_message('sk-' + 'k' * 300 + ' tail') == 'sk-*** tail'
    long = preparation.redact_error_message('y' * 500)
    assert long == 'y' * preparation.MAX_CAUSE_MESSAGE_CHARS + '…'


@pytest.mark.asyncio
async def test_failed_preparation_prints_and_stores_redacted_detail(tmp_path, monkeypatch, capsys):
    failure = _provider_connection_failure()
    class Engine:
        def __init__(self, *args, **kwargs):
            self.vision_endpoint = {'host': 'free.example.test', 'model': 'vision-model-1'}
            self.last_vision_request = {'window_start': 0, 'images': 22, 'image_bytes': 1500000,
                                        'text_chars': 9000, 'request_bytes': 1520000}
        async def vision_config(self):
            return {}
        async def prepare(self, *args, **kwargs):
            raise failure
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result['id']]
    assert job['error'] == 'APIConnectionError'
    detail = job['error_detail']
    assert detail['vision_host'] == 'free.example.test'
    assert detail['vision_model'] == 'vision-model-1'
    assert detail['last_vision_request']['images'] == 22
    assert [layer['type'] for layer in detail['chain']] == ['APIConnectionError', 'ReadError', 'ConnectionResetError']
    printed = capsys.readouterr().out
    assert 'Watch preparation failed: APIConnectionError' in printed
    assert 'ConnectionResetError' in printed and '"request_bytes": 1520000' in printed
    assert 'sk-live' not in printed and 'abc.def-ghi' not in printed


@pytest.mark.asyncio
async def test_failure_before_engine_exists_still_reports_chain(tmp_path, monkeypatch):
    class Engine:
        def __init__(self, *args, **kwargs):
            raise OSError(28, 'No space left on device')
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    detail = preparation.jobs[result['id']]['error_detail']
    assert detail == {'chain': [{'type': 'OSError', 'message': '[Errno 28] No space left on device', 'errno': 28}]}


def test_error_message_hides_home_directory(monkeypatch):
    monkeypatch.setenv('HOME', '/home/alice')
    monkeypatch.setenv('USERPROFILE', '/home/alice')
    message = preparation.redact_error_message("[Errno 2] No such file: '/home/alice/Documents/N.E.K.O/x.jpg'")
    assert message == "[Errno 2] No such file: '~/Documents/N.E.K.O/x.jpg'"


@pytest.mark.parametrize('raw,leak,kept', [
    ('headers x-api-key: abc123 sent', 'abc123', 'x-api-key: ***'),
    ('{"api_key": "AIzaSyXYZ"}', 'AIzaSyXYZ', '"api_key": ***}'),
    ("{'api_key': 'abc'}", "'abc'", "'api_key': ***}"),
    ("api_key='abc def' next", 'def', "api_key=*** next"),
    ('Cookie: session=abc123', 'abc123', 'Cookie: ***'),
    ('Cookie: theme=dark; sessionid=private123\nnext', 'private123', 'Cookie: ***\nnext'),
    ('set-cookie: a=1; sid=zzz9; Path=/', 'zzz9', 'set-cookie: ***'),
    ('Cookie: "sessionid=private123; theme=dark" next', 'private123', 'Cookie: ***'),
    ("Cookie:  'sid=" + 'V' * 300 + "' next", 'VVV', 'Cookie:  ***'),
    ('Cookie: theme="dark"; sessionid=private123\nnext', 'private123', 'Cookie: ***\nnext'),
    ("{'Cookie': 'a=1', 'Set-Cookie': 'sid=q9'}", 'q9', "'Cookie': ***"),
    ('Cookie: "sid=' + 'W' * 3000, 'WWW', 'Cookie: ***'),
    ("api_key='" + 'T' * 300 + "' tail", 'TTT', 'api_key=*** tail'),
    ('token="' + 'U' * 3000, 'UUU', 'token=***'),
    ('api_key = abc rest', 'abc rest', 'api_key = *** rest'),
    ('password=hunter2&x=1', 'hunter2', 'password=***'),
    ('client_secret=s3cr3t', 's3cr3t', 'client_secret=***'),
    ('Authorization: Basic dXNlcjpwYXNz', 'dXNlcjpwYXNz', 'Authorization: ***'),
    ('authorization=Bearer tok', 'tok', 'authorization=***'),
    ('proxy said bearer abc.def', 'abc.def', 'Bearer ***'),
])
def test_error_message_masks_common_credential_formats(raw, leak, kept):
    message = preparation.redact_error_message(raw)
    assert leak not in message
    assert kept in message


def test_provider_status_errors_keep_status_but_not_the_echoed_body():
    import httpx
    import openai
    request = httpx.Request('POST', 'https://api.example.test/v1/chat/completions')
    body = {'error': {'message': 'content flagged: 私密弹幕内容', 'code': 'content_filter'}}
    response = httpx.Response(400, request=request, json=body)
    exc = openai.BadRequestError('Error code: 400 - ' + str(body), response=response, body=body['error'])
    [layer] = preparation.error_cause_chain(exc)
    assert layer == {'type': 'BadRequestError', 'errno': None, 'status_code': 400,
                     'code': 'content_filter', 'message': None}


def test_windows_home_paths_are_hidden_even_when_repr_escaped(monkeypatch):
    monkeypatch.setattr(preparation.os.path, 'expanduser', lambda _path: 'C:\\Users\\bob')
    message = preparation.redact_error_message(OSError(2, 'No such file', 'C:\\Users\\bob\\AppData\\x.jpg'))
    assert 'bob' not in message
    assert message.startswith("[Errno 2] No such file: '~")


def test_huge_messages_are_bounded_before_redaction():
    import time
    started = time.perf_counter()
    for raw in ('a-' * 200000, 'data:' * 50000, 'x' * 400000):
        assert len(preparation.redact_error_message(raw)) <= preparation.MAX_CAUSE_MESSAGE_CHARS + 1
    # Linear-time redaction takes milliseconds; the unbounded patterns took seconds.
    assert time.perf_counter() - started < 2


def test_unprintable_exception_keeps_its_type():
    class Broken(Exception):
        def __str__(self):
            raise RuntimeError('broken __str__')
    assert preparation.error_cause_chain(Broken()) == [{'type': 'Broken', 'errno': None, 'message': None}]


@pytest.mark.asyncio
async def test_failure_detail_errors_still_end_the_job(tmp_path, monkeypatch):
    class Engine:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('boom')
    def broken_detail(*args, **kwargs):
        raise TypeError('detail failed')
    monkeypatch.setattr(preparation, 'preparation_error_detail', broken_detail)
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result['id']]
    assert job['status'] == 'error' and job['error'] == 'RuntimeError'
    assert job['error_detail'] == {'chain': [{'type': 'RuntimeError'}], 'detail_error': 'TypeError'}


def test_endpoint_fields_are_typed_and_bounded():
    engine = SimpleNamespace(vision_endpoint={'host': 5, 'model': 'm-sk-abc ' + 'x' * 300}, last_vision_request=None)
    detail = preparation.preparation_error_detail(ValueError('x'), engine)
    assert detail['vision_host'] is None
    assert len(detail['vision_model']) <= 100
    assert 'last_vision_request' not in detail


def test_cause_chain_ignores_exception_truthiness():
    class Empty(Exception):
        def __len__(self):
            return 0
    inner = Empty('inner')
    outer = RuntimeError('outer')
    outer.__cause__ = inner
    inner.__cause__ = ValueError('root')
    assert [layer['type'] for layer in preparation.error_cause_chain(outer)] == ['RuntimeError', 'Empty', 'ValueError']


def test_cause_chain_honours_from_none():
    try:
        try:
            raise ValueError('inner secret content')
        except ValueError:
            raise RuntimeError('outer') from None
    except RuntimeError as exc:
        chain = preparation.error_cause_chain(exc)
    assert chain == [{'type': 'RuntimeError', 'errno': None, 'message': 'outer'}]


def test_openai_status_error_hides_the_suppressed_httpx_error():
    import httpx
    import openai
    request = httpx.Request('POST', 'https://api.example.test/v1/chat/completions')
    response = httpx.Response(413, request=request, json={'error': {'message': 'echoed 弹幕', 'code': 'too_large'}})
    try:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            # Same shape as the SDK: ``raise self._make_status_error_from_response(...) from None``.
            raise openai.APIStatusError('Error code: 413', response=response, body={'code': 'too_large'}) from None
    except openai.APIStatusError as exc:
        chain = preparation.error_cause_chain(exc)
    assert [layer['type'] for layer in chain] == ['APIStatusError']
    assert chain[0]['status_code'] == 413


def test_lone_surrogates_do_not_break_utf8_output():
    message = preparation.redact_error_message('bad-\udcff-name')
    assert message.encode('utf-8') == b'bad-\\udcff-name'


@pytest.mark.asyncio
async def test_surrogate_in_failure_detail_still_prints_and_persists(tmp_path, monkeypatch, capsys):
    import json
    class Engine:
        def __init__(self, root, *args, **kwargs):
            self.root = root
        async def vision_config(self):
            return {}
        async def prepare(self, job, *args, **kwargs):
            (self.root / job['id']).mkdir(parents=True)
            raise RuntimeError('decoded name frame-\udcff.jpg')
    imported = []
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library', lambda: SimpleNamespace(
        root=tmp_path, import_sources=lambda *args, **kwargs: imported.append(True)))
    monkeypatch.setattr(preparation, 'tasks', set())
    monkeypatch.setattr(preparation, 'jobs', {})
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready,
                              game_speech_audio_cache_identity=lambda *args, **kwargs: ('key', 'voice'))
    result = await preparation.prepare('video', manager, 'cat')
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result['id']]
    assert job['stage'] == 'Preparation failed' and imported
    saved = json.loads((tmp_path / 'preparations' / result['id'] / 'timeline.json').read_text(encoding='utf-8'))
    assert saved['error_detail']['chain'][0]['message'] == 'decoded name frame-\\udcff.jpg'
    assert 'Watch preparation failed: RuntimeError' in capsys.readouterr().out

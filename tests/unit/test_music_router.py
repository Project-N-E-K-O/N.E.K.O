from types import SimpleNamespace

import pytest
from starlette.requests import Request

from main_routers import music_router


@pytest.fixture(autouse=True)
def _reset_music_proxy_cache():
    """Isolate every test in this module from ``music_router.MUSIC_PROXY_CACHE``.

    The cache is a process-wide ``TTLCache`` living in product code, and
    ``proxy_music`` serves a hit before it validates anything. Tests here reuse
    a handful of URLs, so a test that leaves an entry behind makes a later test
    on the same URL take the cache branch and never reach the code it asserts
    on. Autouse + module-wide so new tests are covered without opting in.
    """
    # Specific trigger: test_music_proxy_streams_small_file_then_caches_complete_body caches
    # https://freemusicarchive.org/song.mp3; when random order puts it before
    # test_music_proxy_rejects_unsafe_redirect, the latter gets 200 HIT instead of 403.
    music_router.MUSIC_PROXY_CACHE.clear()
    from utils import music_audio
    music_audio._unavailable_hosts.clear()
    try:
        yield
    finally:
        music_router.MUSIC_PROXY_CACHE.clear()
        music_audio._unavailable_hosts.clear()


async def _read_streaming_body(response):
    body = bytearray()
    async for chunk in response.body_iterator:
        body.extend(chunk)
    return bytes(body)


@pytest.mark.asyncio
@pytest.mark.parametrize('recover',[True,False])
async def test_music_proxy_retries_connection_failure_before_headers(monkeypatch,recover):
    import httpx
    real_client=httpx.AsyncClient
    count=0
    clients=[]
    def handler(request):
        nonlocal count
        count+=1
        if count==1 or not recover:
            raise httpx.ConnectError('temporary connection failure',request=request)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=b'ID3'+b'\0'*1021)
    def factory(**kwargs):
        value=real_client(transport=httpx.MockTransport(handler),**kwargs)
        clients.append(value)
        return value
    monkeypatch.setattr(music_router.httpx,'AsyncClient',factory)
    response=await music_router.proxy_music('https://freemusicarchive.org/chill.mp3',Request({'type':'http','headers':[(b'range',b'bytes=0-1023')]}))
    assert count==2
    if recover:
        assert response.status_code==206
        assert (await _read_streaming_body(response)).startswith(b'ID3')
    else:
        assert response.status_code==502
    assert all(c.is_closed for c in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected',[
    ('missing-location',502),('redirect-loop',502),('html',502),('denied',403),
    ('read-timeout',504),('unexpected',500),('oversize-range',413),
    ('malformed-length',206),('malformed-range',206),
])
async def test_proxy_rejects_broken_upstream_and_closes_resources(monkeypatch,mode,expected):
    import httpx
    real_client=httpx.AsyncClient
    clients=[]
    calls=[]
    def handler(request):
        calls.append(request)
        if mode=='missing-location': return httpx.Response(302)
        if mode=='redirect-loop': return httpx.Response(302,headers={'location':str(request.url)})
        if mode=='html': return httpx.Response(200,headers={'content-type':'text/html'},content=b'<html>no music</html>')
        if mode=='denied': return httpx.Response(403)
        if mode=='read-timeout': raise httpx.ReadTimeout('stalled headers',request=request)
        if mode=='unexpected': raise RuntimeError('unexpected provider adapter failure')
        headers={'content-type':'audio/mpeg'}
        if mode=='oversize-range': headers['content-range']=f'bytes 0-1023/{music_router.MAX_MUSIC_SIZE+1}'
        if mode=='malformed-length': headers['content-length']='not-a-number'
        if mode=='malformed-range': headers['content-range']='bytes 0-1023/not-a-number'
        return httpx.Response(206,headers=headers,content=b'ID3'+b'\0'*1021)
    def factory(**kwargs):
        client=real_client(transport=httpx.MockTransport(handler),**kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(music_router.httpx,'AsyncClient',factory)
    response=await music_router.proxy_music('https://freemusicarchive.org/checked.mp3',Request({'type':'http','headers':[]}))
    assert response.status_code==expected
    if expected==206: assert (await _read_streaming_body(response)).startswith(b'ID3')
    assert len(calls)==(10 if mode=='redirect-loop' else 1)
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_proxy_rejects_missing_url_without_opening_client(monkeypatch):
    monkeypatch.setattr(music_router.httpx,'AsyncClient',lambda **kwargs:pytest.fail('no network for an empty URL'))
    response=await music_router.proxy_music('',Request({'type':'http','headers':[]}))
    assert response.status_code==400


class CookieRecorder:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        if key not in self.values:
            raise KeyError(key)
        del self.values[key]


class FailingCookieRecorder:
    def set(self, key, value):
        raise RuntimeError("detached jar")


def test_sync_pyncm_session_cookies_uses_modern_session_cookie_jar():
    session = SimpleNamespace(cookies=CookieRecorder(), csrf_token="old-csrf")

    cookies = {"MUSIC_U": "token", "__csrf": "new-csrf"}
    assert music_router._sync_pyncm_session_cookies(session, cookies) is True
    assert session.cookies.values == cookies
    assert session.csrf_token == "new-csrf"


def test_sync_pyncm_session_cookies_supports_legacy_client_cookie_jar():
    legacy_client = SimpleNamespace(cookies=CookieRecorder())
    session = SimpleNamespace(client=legacy_client)

    assert music_router._sync_pyncm_session_cookies(session, {"MUSIC_U": "token"}) is True
    assert legacy_client.cookies.values == {"MUSIC_U": "token"}


def test_sync_pyncm_session_cookies_falls_back_when_session_cookies_is_not_mutable():
    legacy_client = SimpleNamespace(cookies=CookieRecorder())
    session = SimpleNamespace(cookies=object(), client=legacy_client)

    assert music_router._sync_pyncm_session_cookies(session, {"MUSIC_U": "token"}) is True
    assert legacy_client.cookies.values == {"MUSIC_U": "token"}


def test_sync_pyncm_session_cookies_writes_all_mutable_cookie_jars():
    session_cookies = CookieRecorder()
    client_cookies = CookieRecorder()
    session = SimpleNamespace(
        cookies=session_cookies,
        client=SimpleNamespace(cookies=client_cookies),
    )

    assert music_router._sync_pyncm_session_cookies(session, {"MUSIC_U": "token"}) is True
    assert session_cookies.values == {"MUSIC_U": "token"}
    assert client_cookies.values == {"MUSIC_U": "token"}


def test_sync_pyncm_session_cookies_continues_after_setter_failure():
    client_cookies = CookieRecorder()
    session = SimpleNamespace(
        cookies=FailingCookieRecorder(),
        client=SimpleNamespace(cookies=client_cookies),
    )

    assert music_router._sync_pyncm_session_cookies(session, {"MUSIC_U": "token"}) is True
    assert client_cookies.values == {"MUSIC_U": "token"}


def test_sync_pyncm_session_cookies_removes_stale_auth_and_preserves_other_cookies():
    cookies = CookieRecorder({
        "MUSIC_U": "old-token",
        "__csrf": "old-csrf",
        "NMTID": "keep-me",
    })
    session = SimpleNamespace(cookies=cookies, csrf_token="old-csrf")

    assert music_router._sync_pyncm_session_cookies(session, {}) is True
    assert cookies.values == {"NMTID": "keep-me"}
    assert session.csrf_token == ""


@pytest.mark.asyncio
async def test_play_netease_music_syncs_cookies_without_session_client(monkeypatch):
    session = SimpleNamespace(cookies=CookieRecorder())

    async def fake_get_track_audio(song_ids):
        assert song_ids == [2070160351]
        return {"data": [{"url": "https://m7.music.126.net/song.mp3"}]}

    monkeypatch.setattr(
        music_router,
        "pyncm_async",
        SimpleNamespace(GetCurrentSession=lambda: session),
    )
    monkeypatch.setattr(music_router, "GetTrackAudio", fake_get_track_audio)
    monkeypatch.setattr(music_router, "_PYNCM_AVAILABLE", True)
    monkeypatch.setattr(
        music_router,
        "load_cookies_from_file",
        lambda platform: {"MUSIC_U": "token"} if platform == "netease" else {},
    )

    response = await music_router.play_netease_music("2070160351")

    assert response.status_code == 307
    assert response.headers["location"] == "/api/music/proxy?url=https%3A%2F%2Fm7.music.126.net%2Fsong.mp3&netease_id=2070160351"
    assert session.cookies.values == {"MUSIC_U": "token"}


@pytest.mark.asyncio
async def test_play_netease_music_rejects_unplayable_public_fallback(monkeypatch):
    async def fake_probe(url):
        assert url == "https://music.163.com/song/media/outer/url?id=123.mp3"
        return False

    monkeypatch.setattr(music_router, "_ensure_pyncm", lambda: False)
    monkeypatch.setattr(music_router, "_probe_audio_url", fake_probe)

    response = await music_router.play_netease_music("123")

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_play_netease_music_uses_verified_public_fallback(monkeypatch):
    async def fake_probe(_url):
        return True

    monkeypatch.setattr(music_router, "_ensure_pyncm", lambda: False)
    monkeypatch.setattr(music_router, "_probe_audio_url", fake_probe)

    response = await music_router.play_netease_music("123")

    assert response.status_code == 307
    assert response.headers["location"] == "/api/music/proxy?url=https%3A%2F%2Fmusic.163.com%2Fsong%2Fmedia%2Fouter%2Furl%3Fid%3D123.mp3&netease_id=123"


@pytest.mark.asyncio
async def test_music_proxy_forwards_range_and_preserves_partial_response(monkeypatch):
    sent_requests = []
    response_closed = False
    client_closed = False

    class FakeResponse:
        status_code = 206
        headers = {
            # Several music CDNs return generic binary media even for valid MP3s.
            "Content-Type": "application/octet-stream",
            "Content-Length": "10",
            "Content-Range": "bytes 0-9/100",
            "Accept-Ranges": "bytes",
        }

        async def aclose(self):
            nonlocal response_closed
            response_closed = True

        async def aiter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"0123456789"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def aclose(self):
            nonlocal client_closed
            client_closed = True

        def build_request(self, method, url, headers):
            request = SimpleNamespace(method=method, url=url, headers=headers)
            sent_requests.append(request)
            return request

        async def send(self, _request, stream):
            assert stream is True
            return FakeResponse()

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/music/proxy",
        "headers": [(b"range", b"bytes=0-9")],
    })

    response = await music_router.proxy_music(
        "https://freemusicarchive.org/track/example/stream/?Policy=a%2Fb%26c", request
    )

    assert sent_requests[0].url == "https://freemusicarchive.org/track/example/stream/?Policy=a%2Fb%26c"
    assert sent_requests[0].headers["Range"] == "bytes=0-9"
    assert sent_requests[0].headers["Referer"] == "https://freemusicarchive.org/"
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-9/100"
    assert response.headers["accept-ranges"] == "bytes"
    assert "content-length" not in response.headers
    assert await _read_streaming_body(response) == b"0123456789"
    assert len(sent_requests) == 1
    assert response_closed is True
    assert client_closed is True


@pytest.mark.asyncio
async def test_music_proxy_yields_first_chunk_before_upstream_finishes(monkeypatch):
    can_finish = False

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg"}

        async def aclose(self):
            return None

        async def aiter_bytes(self, chunk_size):
            nonlocal can_finish
            yield b"first"
            assert can_finish is True
            yield b"second"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, method, url, headers):
            return SimpleNamespace(method=method, url=url, headers=headers)

        async def send(self, _request, stream):
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    response = await music_router.proxy_music("https://freemusicarchive.org/song.mp3", request)
    iterator = response.body_iterator.__aiter__()

    assert await iterator.__anext__() == b"first"
    assert "https://freemusicarchive.org/song.mp3" not in music_router.MUSIC_PROXY_CACHE
    can_finish = True
    assert await iterator.__anext__() == b"second"
    with pytest.raises(StopAsyncIteration):
        await iterator.__anext__()


@pytest.mark.asyncio
async def test_music_proxy_streams_small_file_then_caches_complete_body(monkeypatch):
    send_count = 0

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "6"}

        async def aclose(self):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b"abc"
            yield b"def"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, method, url, headers):
            return SimpleNamespace(method=method, url=url, headers=headers)

        async def send(self, _request, stream):
            nonlocal send_count
            send_count += 1
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    url = "https://freemusicarchive.org/song.mp3"
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    response = await music_router.proxy_music(url, request)
    assert url not in music_router.MUSIC_PROXY_CACHE
    assert await _read_streaming_body(response) == b"abcdef"
    assert music_router.MUSIC_PROXY_CACHE[url]["body"] == b"abcdef"

    cached_response = await music_router.proxy_music(url, request)
    assert cached_response.body == b"abcdef"
    assert cached_response.headers["x-cache"] == "HIT"
    assert send_count == 1


@pytest.mark.asyncio
async def test_music_proxy_does_not_cache_large_or_interrupted_stream(monkeypatch):
    responses = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "3"}

        def __init__(self, interrupted=False):
            self.interrupted = interrupted

        async def aclose(self):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b"abc"
            if self.interrupted:
                raise RuntimeError("upstream disconnected")
            yield b"def"

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, method, url, headers):
            return SimpleNamespace(method=method, url=url, headers=headers)

        async def send(self, _request, stream):
            return responses.pop(0)

        async def aclose(self):
            return None

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(music_router, "STREAMING_SIZE_THRESHOLD", 4)
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    large_url = "https://freemusicarchive.org/large.mp3"
    responses.append(FakeResponse())
    assert await _read_streaming_body(await music_router.proxy_music(large_url, request)) == b"abcdef"
    assert large_url not in music_router.MUSIC_PROXY_CACHE

    interrupted_url = "https://freemusicarchive.org/interrupted.mp3"
    responses.append(FakeResponse(interrupted=True))
    interrupted_response = await music_router.proxy_music(interrupted_url, request)
    interrupted_iterator = interrupted_response.body_iterator.__aiter__()
    assert await interrupted_iterator.__anext__() == b"abc"
    with pytest.raises(RuntimeError, match="upstream disconnected"):
        await interrupted_iterator.__anext__()
    assert interrupted_url not in music_router.MUSIC_PROXY_CACHE

    oversize_url = "https://freemusicarchive.org/unknown-size.mp3"
    monkeypatch.setattr(music_router, "MAX_MUSIC_SIZE", 4)
    responses.append(FakeResponse())
    oversize_response = await music_router.proxy_music(oversize_url, request)
    assert "content-length" not in oversize_response.headers
    oversize_iterator = oversize_response.body_iterator.__aiter__()
    assert await oversize_iterator.__anext__() == b"abc"
    with pytest.raises(music_router._MusicStreamLimitExceeded):
        await oversize_iterator.__anext__()
    assert oversize_url not in music_router.MUSIC_PROXY_CACHE


@pytest.mark.asyncio
async def test_music_proxy_rejects_declared_oversize_before_streaming(monkeypatch):
    response_closed = False
    client_closed = False

    class FakeResponse:
        status_code = 200
        headers = {
            "Content-Type": "audio/mpeg",
            "Content-Length": str(music_router.MAX_MUSIC_SIZE + 1),
        }

        async def aclose(self):
            nonlocal response_closed
            response_closed = True

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, method, url, headers):
            return SimpleNamespace(method=method, url=url, headers=headers)

        async def send(self, _request, stream):
            return FakeResponse()

        async def aclose(self):
            nonlocal client_closed
            client_closed = True

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    response = await music_router.proxy_music("https://freemusicarchive.org/huge.mp3", request)

    assert response.status_code == 413
    assert response_closed is True
    assert client_closed is True


@pytest.mark.asyncio
async def test_music_proxy_rejects_plain_http_initial_url():
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    response = await music_router.proxy_music("http://freemusicarchive.org/song.mp3", request)

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_url",
    ["https://example.invalid/song.mp3", "http://freemusicarchive.org/song.mp3"],
)
async def test_music_proxy_rejects_unsafe_redirect(monkeypatch, redirect_url):
    client_closed = False

    class RedirectResponse:
        status_code = 302
        headers = {"location": redirect_url}

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, method, url, headers):
            return SimpleNamespace(method=method, url=url, headers=headers)

        async def send(self, _request, stream):
            return RedirectResponse()

        async def aclose(self):
            nonlocal client_closed
            client_closed = True

    monkeypatch.setattr(music_router.httpx, "AsyncClient", FakeClient)
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    response = await music_router.proxy_music("https://freemusicarchive.org/song.mp3", request)

    assert response.status_code == 403
    assert client_closed is True


@pytest.mark.parametrize(
    "content_type",
    ["audio/mpeg", "video/mp4", "application/octet-stream", "binary/octet-stream"],
)
def test_playable_audio_content_type_is_shared_across_proxy_and_probe(content_type):
    assert music_router._is_playable_audio_content_type(content_type) is True


@pytest.mark.asyncio
async def test_public_search_strips_internal_audio_url_without_mutating_candidates(monkeypatch):
    from copy import deepcopy
    from unittest.mock import AsyncMock
    row = {'name': 'Song', 'url': '/api/music/play/netease/1', '_audio_url': 'https://m7.music.126.net/private.mp3'}
    original = {'success': True, 'data': [row], 'best_match': {'status': 'exact', 'resource': row}}
    snapshot = deepcopy(original)
    monkeypatch.setattr(music_router, 'fetch_music_content', AsyncMock(return_value=original))
    result = await music_router.search_music(query='Song', limit=5)
    assert '_audio_url' not in result['data'][0]
    assert '_audio_url' not in result['best_match']['resource']
    assert original == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize('pyncm_available', [False, True])
async def test_fallback_probe_queue_pressure_does_not_mark_song_unavailable(monkeypatch, pyncm_available):
    from unittest.mock import AsyncMock
    from utils import music_audio as audio
    monkeypatch.setattr(audio, '_availability', audio.OrderedDict())
    monkeypatch.setattr(music_router, '_ensure_pyncm', lambda: pyncm_available)
    monkeypatch.setattr(music_router, 'pyncm_async', SimpleNamespace(GetCurrentSession=lambda: SimpleNamespace(cookies=CookieRecorder())))
    monkeypatch.setattr(music_router, 'load_cookies_from_file', lambda platform: {})
    monkeypatch.setattr(music_router, 'GetTrackAudio', AsyncMock(return_value={'data': [{'url': None}]}))
    monkeypatch.setattr(music_router, '_probe_audio_url', AsyncMock(return_value=None))
    response = await music_router.play_netease_music('123')
    assert response.status_code == 503
    assert not audio._availability
    assert not audio._unavailable_hosts


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['denied', 'connect', 'success'])
async def test_netease_playback_failure_invalidates_checked_song_and_only_its_owner(monkeypatch, failure):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from unittest.mock import AsyncMock
    from urllib.parse import urlparse, parse_qs
    from utils import music_audio as audio
    checked = 'https://m7.music.126.net/checked.mp3?token=old'
    refreshed = 'http://m7.music.126.net/play.mp3?token=new'
    row = {'url': '/api/music/play/netease/1', '_audio_url': checked}
    monkeypatch.setattr(audio, '_availability', audio.OrderedDict())
    monkeypatch.setattr(audio, '_probe_slots', asyncio.Semaphore(3))
    monkeypatch.setattr(music_router, '_ensure_pyncm', lambda: True)
    monkeypatch.setattr(music_router, 'pyncm_async', SimpleNamespace(GetCurrentSession=lambda: SimpleNamespace(cookies=CookieRecorder())))
    monkeypatch.setattr(music_router, 'load_cookies_from_file', lambda platform: {})
    monkeypatch.setattr(music_router, 'GetTrackAudio', AsyncMock(return_value={'data': [{'url': refreshed}]}))
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == '/play.mp3':
            assert request.headers['range'] == 'bytes=123-456'
            if failure == 'connect':
                raise httpx.ConnectError('offline', request=request)
            if failure == 'denied':
                return httpx.Response(403)
        return httpx.Response(206, headers={'content-type': 'audio/mpeg'}, content=b'ID3' + b'\0' * 300)
    app = FastAPI()
    app.include_router(music_router.router)
    real_client = httpx.AsyncClient
    async with real_client(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as api:
        monkeypatch.setattr(audio.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
        assert await audio.filter_playable_candidates([row]) == [row]
        resolved = await api.get('/api/music/play/netease/1')
        location = resolved.headers['location']
        assert urlparse(location).path == '/api/music/proxy'
        assert parse_qs(urlparse(location).query)['url'] == [refreshed.replace('http://', 'https://', 1)]
        response = await api.get(location, headers={'Range': 'bytes=123-456'})
        assert response.status_code == {'connect': 502, 'denied': 403, 'success': 206}[failure]
        assert bool(await audio.filter_playable_candidates([row])) is (failure == 'success')
        if failure == 'success':
            assert response.content == b'ID3' + b'\0' * 300
        other = {'url': '/api/music/play/netease/2', '_audio_url': checked}
        assert bool(await audio.filter_playable_candidates([other])) is (failure != 'connect')

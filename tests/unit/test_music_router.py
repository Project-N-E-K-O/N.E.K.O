from types import SimpleNamespace

import pytest
from starlette.requests import Request

from main_routers import music_router


async def _read_streaming_body(response):
    body = bytearray()
    async for chunk in response.body_iterator:
        body.extend(chunk)
    return bytes(body)


class CookieRecorder:
    def __init__(self):
        self.values = {}

    def set(self, key, value):
        self.values[key] = value


class FailingCookieRecorder:
    def set(self, key, value):
        raise RuntimeError("detached jar")


def test_sync_pyncm_session_cookies_uses_modern_session_cookie_jar():
    session = SimpleNamespace(cookies=CookieRecorder())

    assert music_router._sync_pyncm_session_cookies(session, {"MUSIC_U": "token"}) is True
    assert session.cookies.values == {"MUSIC_U": "token"}


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
    assert response.headers["location"] == "https://m7.music.126.net/song.mp3"
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
    assert response.headers["location"] == "https://music.163.com/song/media/outer/url?id=123.mp3"


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
        "https://freemusicarchive.org/track/example/stream/", request
    )

    assert sent_requests[0].headers["Range"] == "bytes=0-9"
    assert sent_requests[0].headers["Referer"] == "https://freemusicarchive.org/"
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-9/100"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "10"
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
    music_router.MUSIC_PROXY_CACHE.clear()
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
    music_router.MUSIC_PROXY_CACHE.clear()
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
        headers = {"Content-Type": "audio/mpeg"}

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
    music_router.MUSIC_PROXY_CACHE.clear()
    request = Request({"type": "http", "method": "GET", "path": "/api/music/proxy", "headers": []})

    large_url = "https://freemusicarchive.org/large.mp3"
    responses.append(FakeResponse())
    assert await _read_streaming_body(await music_router.proxy_music(large_url, request)) == b"abcdef"
    assert large_url not in music_router.MUSIC_PROXY_CACHE

    interrupted_url = "https://freemusicarchive.org/interrupted.mp3"
    responses.append(FakeResponse(interrupted=True))
    assert await _read_streaming_body(await music_router.proxy_music(interrupted_url, request)) == b"abc"
    assert interrupted_url not in music_router.MUSIC_PROXY_CACHE

    oversize_url = "https://freemusicarchive.org/unknown-size.mp3"
    monkeypatch.setattr(music_router, "MAX_MUSIC_SIZE", 4)
    responses.append(FakeResponse())
    assert await _read_streaming_body(await music_router.proxy_music(oversize_url, request)) == b"abc"
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
async def test_music_proxy_rejects_redirect_outside_allowlist(monkeypatch):
    client_closed = False

    class RedirectResponse:
        status_code = 302
        headers = {"location": "https://example.invalid/song.mp3"}

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

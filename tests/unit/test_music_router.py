from types import SimpleNamespace

import pytest
from starlette.requests import Request

from main_routers import music_router


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
            return None

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

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

    response = await music_router.proxy_music("https://music.163.com/example.mp3", request)

    assert sent_requests[0].headers["Range"] == "bytes=0-9"
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-9/100"
    assert response.headers["accept-ranges"] == "bytes"

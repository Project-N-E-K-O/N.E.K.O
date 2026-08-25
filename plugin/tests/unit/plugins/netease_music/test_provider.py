from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from plugin.plugins.netease_music.models import PlayRequest, SongCandidate
from plugin.plugins.netease_music import provider as provider_module
from plugin.plugins.netease_music.provider import (
    MediaUnavailableError,
    NeteaseMusicProvider,
    ProviderError,
    ProviderSecurityError,
    select_first_exact_match,
)


def _song(
    song_id: int,
    name: str,
    artist: str,
    *,
    album: str = "Album",
    fee: int | None = 0,
) -> dict[str, object]:
    return {
        "id": song_id,
        "name": name,
        "artists": [{"name": part} for part in artist.split(" / ")],
        "album": {"name": album},
        "fee": fee,
    }


def _search_response(songs: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "result": {"songs": songs}})


def _provider(handler: Callable[[httpx.Request], httpx.Response]) -> NeteaseMusicProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NeteaseMusicProvider(client)


def test_owned_client_has_fixed_security_options(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        is_closed = False

    def make_client(**kwargs: object) -> DummyClient:
        captured.update(kwargs)
        return DummyClient()

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", make_client)
    NeteaseMusicProvider()

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (3.0, 5.0, 5.0, 3.0)
    assert captured["follow_redirects"] is False
    assert captured["proxy"] is None
    assert captured["trust_env"] is False


def test_play_request_strips_and_limits_query() -> None:
    assert PlayRequest(query="  晴天 周杰伦  ").query == "晴天 周杰伦"
    with pytest.raises(ValidationError):
        PlayRequest(query="   ")
    with pytest.raises(ValidationError):
        PlayRequest(query="歌" * 101)
    with pytest.raises(ValidationError):
        PlayRequest(query="晴天", url="https://example.test/audio.mp3")

    assert PlayRequest.model_json_schema()["properties"]["query"]["description"] == {
        "$i18n": "entry.play.param.query",
        "default": "歌曲名，可附带歌手或版本",
    }


@pytest.mark.asyncio
async def test_search_uses_fixed_anonymous_request_and_parses_sanitized_results() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        songs = [
            _song(1, "  晴\u200b 天\x00 ", "周杰伦", album=" 叶惠美 ", fee=8),
            _song(2, "夜曲", "周杰伦 / 林俊杰"),
            _song(2, "duplicate", "artist"),
            _song(3, "稻香", "周杰伦"),
            _song(4, "七里香", "周杰伦"),
            _song(5, "简单爱", "周杰伦"),
            _song(6, "不能说的秘密", "周杰伦"),
        ]
        return _search_response(songs)

    provider = _provider(handler)
    results = await provider.search("  晴天  ")

    assert len(results) == 5
    assert results[0] == SongCandidate(
        1,
        "晴 天",
        "周杰伦",
        "叶惠美",
        8,
        ("周杰伦",),
    )
    assert results[1].artist == "周杰伦 / 林俊杰"
    assert [candidate.song_id for candidate in results] == [1, 2, 3, 4, 5]
    request = requests[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://music.163.com/api/search/get/web")
    assert request.headers.get("cookie") == ""
    assert request.content == b"s=%E6%99%B4%E5%A4%A9&type=1&offset=0&limit=5"


@pytest.mark.asyncio
async def test_search_skips_invalid_candidates_and_supports_new_field_names() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 200,
                "result": {
                    "songs": [
                        {"id": True, "name": "bad", "artists": [{"name": "artist"}]},
                        {"id": -1, "name": "bad", "artists": [{"name": "artist"}]},
                        {"id": 7, "name": "", "artists": [{"name": "artist"}]},
                        {"id": 8, "name": "no artist", "artists": []},
                        {
                            "id": "9",
                            "name": "valid",
                            "ar": [{"name": "artist"}],
                            "al": {"name": "album"},
                        },
                    ]
                },
            },
        )

    assert await _provider(handler).search("valid") == [
        SongCandidate(9, "valid", "artist", "album", None, ("artist",))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(302, headers={"Location": "https://example.test"}), "redirected"),
        (httpx.Response(503), "HTTP 503"),
        (httpx.Response(200, content=b"not-json"), "invalid JSON"),
        (httpx.Response(200, json={"code": 500}), "invalid API"),
        (httpx.Response(200, json={"code": 200, "result": {"songs": {}}}), "song list"),
    ],
)
async def test_search_rejects_abnormal_responses(
    response: httpx.Response,
    message: str,
) -> None:
    with pytest.raises(ProviderError, match=message):
        await _provider(lambda _request: response).search("query")


@pytest.mark.asyncio
async def test_search_wraps_network_errors_without_leaking_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret https://music.163.com/?token=abc", request=request)

    with pytest.raises(ProviderError, match="search request failed") as caught:
        await _provider(handler).search("query")
    assert "token" not in str(caught.value)


def test_first_exact_match_is_conservative() -> None:
    official = SongCandidate(1, "晴天", "周杰伦", "叶惠美")
    impersonator = SongCandidate(2, "晴天", "周杰伦-", "Other")
    other = SongCandidate(3, "晴天 (Live)", "周杰伦", "Live")

    assert select_first_exact_match("晴天 周杰伦", [official, impersonator, other]) == official
    assert select_first_exact_match("周杰伦 晴天", [official, impersonator, other]) == official
    assert select_first_exact_match("晴天 周杰伦-", [official, impersonator]) == impersonator
    assert select_first_exact_match("晴天", [official, impersonator, other]) == official
    assert select_first_exact_match("晴", [official]) is None
    assert select_first_exact_match("\u300c晴天 周杰伦\u300d", [official]) == official


def test_first_exact_match_accepts_each_artist() -> None:
    duet = SongCandidate(1, "歌曲", "甲 / 乙", artist_names=("甲", "乙"))
    duplicate = SongCandidate(1, "歌曲", "甲 / 乙", artist_names=("甲", "乙"))
    assert select_first_exact_match("歌曲 乙", [duet]) == duet
    assert select_first_exact_match("歌曲 甲", [duet, duplicate]) == duet


def test_first_exact_match_does_not_split_one_artist_name_on_display_separator() -> None:
    slash_artist = SongCandidate(
        1,
        "歌曲",
        "AC / DC",
        artist_names=("AC / DC",),
    )

    assert select_first_exact_match("歌曲 AC / DC", [slash_artist]) == slash_artist
    assert select_first_exact_match("歌曲 AC", [slash_artist]) is None


@pytest.mark.asyncio
async def test_resolve_media_follows_relative_redirect_and_probes_audio() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "music.163.com":
            return httpx.Response(302, headers={"Location": "//m10.music.126.net/file.mp3?token=x"})
        return httpx.Response(206, headers={"Content-Type": "audio/mpeg; charset=binary"})

    media = await _provider(handler).resolve_media(123)

    assert media.hostname == "m10.music.126.net"
    assert media.url == "https://m10.music.126.net/file.mp3?token=x"
    assert [str(request.url) for request in requests] == [
        "https://music.163.com/song/media/outer/url?id=123.mp3",
        "https://m10.music.126.net/file.mp3?token=x",
    ]
    assert all(request.headers["range"] == "bytes=0-0" for request in requests)
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)
    assert all(request.headers["cookie"] == "" for request in requests)


@pytest.mark.asyncio
async def test_resolve_media_upgrades_trusted_http_cdn_without_http_request() -> None:
    schemes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        schemes.append(request.url.scheme)
        if len(schemes) == 1:
            return httpx.Response(
                302,
                headers={"Location": "http://m801.music.126.net:80/file.mp3"},
            )
        return httpx.Response(200, headers={"Content-Type": "application/octet-stream"})

    media = await _provider(handler).resolve_media(1)
    assert media.url == "https://m801.music.126.net/file.mp3"
    assert schemes == ["https", "https"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http://music.163.com/file.mp3",
        "https://evil.test/file.mp3",
        "https://music.126.net.evil.test/file.mp3",
        "https://127.0.0.1/file.mp3",
        "https://user@music.126.net/file.mp3",
        "https://music.126.net:444/file.mp3",
        "https://music.126.net./file.mp3",
    ],
)
async def test_resolve_media_rejects_unsafe_redirects(location: str) -> None:
    provider = _provider(
        lambda _request: httpx.Response(302, headers={"Location": location})
    )
    with pytest.raises(ProviderSecurityError):
        await provider.resolve_media(1)


@pytest.mark.asyncio
async def test_resolve_media_rejects_invalid_song_id_before_network() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    with pytest.raises(ProviderSecurityError):
        await _provider(handler).resolve_media(True)
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(302), "no location"),
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, headers={"Content-Type": "text/html"}), "not audio"),
        (httpx.Response(200), "not audio"),
    ],
)
async def test_resolve_media_rejects_unavailable_responses(
    response: httpx.Response,
    message: str,
) -> None:
    with pytest.raises(MediaUnavailableError, match=message):
        await _provider(lambda _request: response).resolve_media(1)


@pytest.mark.asyncio
async def test_resolve_media_rejects_loops_and_more_than_five_redirects() -> None:
    def loop_handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "music.163.com":
            return httpx.Response(302, headers={"Location": "https://m10.music.126.net/a"})
        return httpx.Response(302, headers={"Location": "https://m10.music.126.net/a"})

    with pytest.raises(MediaUnavailableError, match="loop"):
        await _provider(loop_handler).resolve_media(1)

    calls = 0

    def long_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"Location": f"https://m10.music.126.net/{calls}"},
        )

    with pytest.raises(MediaUnavailableError, match="too many"):
        await _provider(long_handler).resolve_media(1)
    assert calls == 6


@pytest.mark.asyncio
async def test_resolve_media_wraps_network_errors_without_leaking_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret signed_url=abc", request=request)

    with pytest.raises(ProviderError, match="media request failed") as caught:
        await _provider(handler).resolve_media(1)
    assert "signed_url" not in str(caught.value)

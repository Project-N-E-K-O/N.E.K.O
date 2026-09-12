import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx

from main_logic.watch_together import discovery


@pytest.mark.parametrize("seconds,count,expected", [
    (60, 100, False), (60, 101, True), (180, 10000, False),
    (179, 299, True), (0, 1000, False), (-1, 1000, False),
    (float("nan"), 1000, False), (60, float("inf"), False),
    (60, None, False), (None, 1000, False),
    (10**1000, 1000, False), (60, 10**1000, False),
])
def test_strict_thresholds(seconds, count, expected):
    assert discovery.eligible(seconds, count) is expected


def test_long_confirmation_and_changed_duration():
    info = {"duration": 300, "parts": 1, "danmaku": 1000}
    assert discovery.enforce_policy(info)
    info["duration"] = 301
    assert not discovery.enforce_policy(info)
    assert discovery.enforce_policy(info, confirmed_duration=301)
    assert not discovery.enforce_policy(info, confirmed_duration=300)
    with pytest.raises(ValueError):
        discovery.enforce_policy(info, automatic=True, confirmed_duration=301)
    info.update(duration=60, parts=2)
    with pytest.raises(ValueError):
        discovery.enforce_policy(info, automatic=True)


@pytest.mark.parametrize("metadata,actual,confirmed,allowed", [
    (301, 301.25, 301, True), (301, 300.9, 301, True),
    (301, 303, 301, True), (301, 303.01, 301, False), (301, 1200, 301, False),
    (301, 301.25, None, False), (300, 300.01, None, False),
    (300, 300.01, 300, False), (302, 302.1, 301, False),
])
def test_downloaded_duration_preserves_consent_without_float_equality(metadata, actual, confirmed, allowed):
    assert discovery.enforce_download_policy(
        {"duration": actual, "parts": 1, "danmaku": 1000},
        metadata_duration=metadata, confirmed_duration=confirmed) is allowed


@pytest.mark.parametrize("seconds,count,automatic", [
    (180, 1000, True), (179, 298, True), (1200.01, 10000, False),
    (float('nan'), 1000, False), (0, 1000, False),
])
def test_downloaded_limits_cannot_be_bypassed_by_consent(seconds, count, automatic):
    with pytest.raises(ValueError):
        discovery.enforce_download_policy(
            {"duration": seconds, "parts": 1, "danmaku": count},
            metadata_duration=301, confirmed_duration=301, automatic=automatic)


@pytest.mark.asyncio
@pytest.mark.parametrize('stat', [None, [], 7, 'invalid'])
async def test_discovery_skips_malformed_rows_and_accepts_primary_counter(monkeypatch, stat):
    rows = [None, [], {'bvid': {}}, {'bvid': 'bad', 'duration': 60, 'stat': stat},
            {'bvid': 'valid', 'duration': 60, 'video_review': 101, 'stat': stat}]
    search = SimpleNamespace(search_by_type=AsyncMock(return_value={'result': rows}),
        SearchObjectType=SimpleNamespace(VIDEO='video'), OrderVideo=SimpleNamespace(CLICK='click'))
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(search=search, hot=None))
    info = {'duration': 60, 'danmaku': 101, 'parts': 1}
    inspect = AsyncMock(return_value=info)
    monkeypatch.setattr(discovery, 'inspect_video', inspect)
    assert (await discovery.discover('cats'))['video'] == info
    inspect.assert_awaited_once_with('valid')


@pytest.mark.asyncio
async def test_discovery_rechecks_metadata_and_never_relaxes(monkeypatch):
    rows = [{"bvid": "first", "duration": "1:00", "video_review": 101},
            {"bvid": "second", "duration": "2:59", "video_review": 300}]
    search = SimpleNamespace(search_by_type=AsyncMock(return_value={"result": rows}),
                             SearchObjectType=SimpleNamespace(VIDEO="video"),
                             OrderVideo=SimpleNamespace(CLICK="click"))
    monkeypatch.setitem(sys.modules, "bilibili_api", SimpleNamespace(search=search, hot=None))
    inspect = AsyncMock(side_effect=[
        {"duration": 180, "danmaku": 1000, "parts": 1},
        {"duration": 179, "danmaku": 300, "parts": 1}])
    monkeypatch.setattr(discovery, "inspect_video", inspect)
    result = await discovery.discover("cats")
    assert result["video"]["duration"] == 179
    inspect.side_effect = [RuntimeError("unavailable"), {"duration": 60, "danmaku": 101, "parts": 1}]
    assert (await discovery.discover("cats"))["video"]["duration"] == 60
    inspect.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await discovery.discover("cats")
    inspect.reset_mock()
    inspect.side_effect = None
    assert (await discovery.discover("cats", exclude=["first", "second"]))["video"] is None
    inspect.assert_not_awaited()
    assert search.search_by_type.call_args.args == ("cats",)
    inspect.return_value = {"duration": 60, "danmaku": 100, "parts": 1}
    assert (await discovery.discover("cats"))["video"] is None


@pytest.mark.asyncio
async def test_blank_topic_uses_hot_feed(monkeypatch):
    hot = SimpleNamespace(get_hot_videos=AsyncMock(return_value={"list": []}))
    monkeypatch.setitem(sys.modules, "bilibili_api", SimpleNamespace(hot=hot, search=None))
    assert (await discovery.discover(""))["video"] is None
    assert hot.get_hot_videos.await_count == 3


@pytest.mark.asyncio
async def test_prepare_does_not_start_before_confirmation(monkeypatch):
    from main_routers import watch_together_router as routes
    from main_routers import shared_state
    from main_logic.watch_together import preparation
    monkeypatch.setattr(shared_state, "get_config_manager", lambda: SimpleNamespace(load_characters=lambda: {}))
    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {"cat": object()})
    monkeypatch.setattr(discovery, "inspect_video", AsyncMock(return_value={
        "title": "Long video", "url": "https://www.bilibili.com/video/BV1GJ411x7h7?p=1",
        "duration": 301, "parts": 1, "danmaku": 1000}))
    start = AsyncMock(return_value={"id": "new-job"})
    monkeypatch.setattr(preparation, "prepare", start)
    data = {"url": "BV1GJ411x7h7", "lanlan_name": "cat"}
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value=data))
    assert (await routes.prepare_video(request))["confirmation_required"]
    start.assert_not_awaited()
    data["confirmed_duration"] = 301
    assert (await routes.prepare_video(request))["id"] == "new-job"
    start.assert_awaited_once()

@pytest.mark.asyncio
async def test_authoritative_duration_overflow_is_invalid_video(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(
        Credential=lambda: None,
        video=SimpleNamespace(Video=lambda **kwargs: SimpleNamespace(get_info=AsyncMock(
            return_value={'pages': [{'duration': 10**1000}]})))))
    monkeypatch.setattr('utils.web_scraper.platform_helpers._get_bilibili_credential', lambda: None)
    with pytest.raises(ValueError, match='Invalid video duration'):
        await discovery.inspect_video('BV1GJ411x7h7')

@pytest.mark.asyncio
async def test_authoritative_numeric_string_danmaku_has_density(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(
        Credential=lambda: None,
        video=SimpleNamespace(Video=lambda **kwargs: SimpleNamespace(get_info=AsyncMock(
            return_value={'pages': [{'duration': 60}], 'title': 'Video', 'stat': {'danmaku': '101'}})))))
    monkeypatch.setattr('utils.web_scraper.platform_helpers._get_bilibili_credential', lambda: None)
    info = await discovery.inspect_video('BV1GJ411x7h7')
    assert info['danmaku_per_minute'] == 101
    assert discovery.enforce_policy(info, automatic=True)
@pytest.mark.asyncio
@pytest.mark.parametrize('status,location', [
    (429, None), (500, None), (200, None), (302, None),
    (302, 'https://example.com/unusable'),
])
async def test_short_link_provider_failure_is_not_invalid_input(monkeypatch, status, location):
    monkeypatch.setitem(sys.modules, 'bilibili_api', SimpleNamespace(Credential=None, video=None))
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(
        status, headers={'location': location} if location else {}))
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        await discovery.inspect_video('https://b23.tv/test')

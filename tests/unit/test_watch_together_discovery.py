import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.watch_together import discovery


@pytest.mark.parametrize("seconds,count,expected", [
    (60, 100, False), (60, 101, True), (180, 10000, False),
    (179, 299, True), (0, 1000, False), (-1, 1000, False),
    (float("nan"), 1000, False), (60, float("inf"), False),
    (60, None, False), (None, 1000, False),
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
    assert search.search_by_type.call_args.args == ("cats",)
    inspect.side_effect = None
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

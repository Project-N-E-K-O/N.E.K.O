"""Model decisions retain object types and host execution evidence."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest

from brain.music_capabilities import scoped_music_catalog
from plugin.plugins.netease_music.cloud_entry import CLOUD_SCHEMA
from plugin.plugins.netease_music.models import SongCandidate
from plugin.plugins.netease_music.provider import NeteaseMusicProvider


@pytest.mark.parametrize(
    "text",
    [
        "I would like to hear a song",
        "Can you add a song to the queue?",
        "我不喜欢这首歌",
        "取消播放这首歌，我不喜欢",
        "我特别喜欢这首，帮我收藏",
        "收藏这首歌，别忘了",
        "把这首加入本地歌单夜读，不要保存整张歌单",
        "再试试",
        "看看红心歌单，分析我的曲风",
        "play some netease music",
    ],
)
def test_user_language_never_removes_or_selects_operations(text):
    catalog = [
        {
            "id": "netease_music",
            "entries": [
                {"id": "play_netease_music"},
                {
                    "id": "playlist",
                    "input_schema": {
                        "properties": {
                            "action": {"enum": ["next", "save", "save_current"]}
                        }
                    },
                },
                {"id": "cloud_music", "input_schema": deepcopy(CLOUD_SCHEMA)},
            ],
        }
    ]
    original = deepcopy(catalog)
    assert scoped_music_catalog(catalog, text) == original
    proactive = scoped_music_catalog(catalog, text, proactive=True)
    assert proactive[0]["entries"][1]["input_schema"]["properties"]["action"][
        "enum"
    ] == ["next"]
    assert set(
        proactive[0]["entries"][2]["input_schema"]["properties"]["action"]["enum"]
    ) == {"account", "playlists", "tracks", "play", "liked", "daily", "artists"}
    assert catalog == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offer",
    [
        "今天的雨好大，推荐你听听《晴天》吧。",
        "我的建议是：给你放一首《晴天》",
        'Let me play "Yellow" by Coldplay — it is lovely.',
    ],
)
async def test_proactive_offer_goes_through_model(offer):
    from brain.task_executor import DirectTaskExecutor, UserPluginDecision

    executor = DirectTaskExecutor.__new__(DirectTaskExecutor)
    executor.plugin_list_provider = AsyncMock(
        return_value=[
            {"id": "netease_music", "entries": [{"id": "play_netease_music"}]}
        ]
    )
    executor._assess_user_plugin = AsyncMock(
        return_value=UserPluginDecision(has_task=False, can_execute=False)
    )
    result = await executor._analyze_and_execute_inner(
        messages=[{"role": "assistant", "content": offer}],
        lanlan_name="Example",
        agent_flags={"user_plugin_enabled": True},
        proactive=True,
    )
    assert result is None or not result.has_task
    executor._assess_user_plugin.assert_awaited_once()
    assert offer in executor._assess_user_plugin.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [("artist", [2]), ("song", [1])])
async def test_search_type_never_falls_through(kind, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: pytest.fail("Unexpected public playlist search")
        )
    ) as client:
        provider = NeteaseMusicProvider(client)
        provider.search = AsyncMock(
            return_value=[
                SongCandidate(1, "Example", "Other"),
                SongCandidate(2, "Other", "Example"),
            ]
        )
        assert [
            s.song_id for s in (await provider.playlist("Example", search_type=kind))[1]
        ] == expected
        provider.search.return_value = []
        assert (await provider.playlist("Example", search_type=kind))[1] == []


@pytest.mark.asyncio
async def test_public_playlist_does_not_consult_colliding_song():
    requests = []

    def respond(request):
        if request.method == "POST":
            requests.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "result": {"playlists": [{"id": 9, "name": "Example"}]},
                },
            )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "playlist": {
                    "name": "Example",
                    "tracks": [
                        {
                            "id": 2,
                            "name": "Collection Track",
                            "ar": [{"name": "Artist"}],
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = NeteaseMusicProvider(client)
        provider.search = AsyncMock(
            return_value=[SongCandidate(1, "Example", "Example")]
        )
        songs = (await provider.playlist("Example", search_type="playlist"))[1]
        provider.search.assert_not_awaited()
        assert requests[0]["type"] == ["1000"]
        assert [song.song_id for song in songs] == [2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,limit,expected",
    [
        (None, None, (0, 20)),
        (True, False, (0, 20)),
        ("3", "40", (0, 20)),
        (-1, 0, (0, 20)),
        (100001, 51, (0, 20)),
        (12, 999, (12, 20)),
        (3, 50, (3, 50)),
    ],
)
async def test_paging_defaults_invalid_fields_independently(offset, limit, expected):
    from plugin.tests.unit.plugins.netease_music.test_plugin import (
        _make_plugin,
        _Provider,
        _ctx,
    )

    plugin, _, _ = _make_plugin(_Provider())

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    plugin._new_cloud_library = Session
    plugin._cloud_operation = AsyncMock(return_value={"items": [], "total": 0})
    result = await plugin.cloud_music("liked", offset=offset, limit=limit, _ctx=_ctx())
    assert plugin._cloud_operation.call_args.args[5:7] == expected
    assert result["finish"]["data"]["action"] == "liked"


@pytest.mark.asyncio
async def test_queue_facts_survive_actual_result_projection(tmp_path):
    from plugin.tests.unit.plugins.netease_music.test_plugin import (
        _make_plugin,
        _Provider,
        _ctx,
    )
    from plugin.sdk.shared.constants import EVENT_META_ATTR
    from utils.result_parser import parse_plugin_result

    provider = _Provider()
    provider.playlist = AsyncMock(
        return_value=("Example", [SongCandidate(1, "Song", "Artist")])
    )
    plugin, pushes, _ = _make_plugin(provider)
    plugin._queues = {}
    plugin.data_path = lambda: tmp_path
    result = await plugin.playlist(
        "search", name="Example", search_type="artist", _ctx=_ctx()
    )
    assert provider.playlist.call_args.kwargs == {"search_type": "artist"}
    event = json.loads(pushes[0]["parts"][0]["text"].splitlines()[-1])
    assert event["event"] == "queue_ready" and event["player_confirmed"] is False
    assert pushes[0]["visibility"] == []
    for entry in ("playlist", "cloud_music"):
        meta = getattr(getattr(plugin, entry), EVENT_META_ATTR)
        projected = json.loads(
            parse_plugin_result(
                result["finish"]["data"], llm_result_fields=meta.llm_result_fields
            )
        )
        assert projected["status"] == "submitted"
        assert projected["song"]["name"] == "Song"
        assert projected["player_confirmed"] is False


def test_proactive_cap_applies_before_model_assessment(monkeypatch):
    from app.agent_server import api_runtime as api

    monkeypatch.setattr(api, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(api, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    monkeypatch.setattr(api.Modules, "agent_flags", {"user_plugin_enabled": True})
    monkeypatch.setattr(api.Modules, "proactive_analyze_count", {"Example": 2})
    monkeypatch.setattr(api.Modules, "last_proactive_assistant_fingerprint", {})
    monkeypatch.setattr(
        api, "_create_tracked_task", lambda task: pytest.fail("Exceeded cap")
    )
    api._handle_proactive_analyze(
        [{"role": "assistant", "content": "来听听《Example Song》吧"}],
        "Example",
        "Example",
        "test",
    )


@pytest.mark.asyncio
async def test_proactive_first_track_ended_advances_real_plugin_queue(monkeypatch):
    import asyncio
    from unittest.mock import MagicMock
    from main_logic import music_playback
    from plugin.tests.unit.plugins.netease_music.test_plugin import (
        _make_plugin,
        _Provider,
        _ctx,
        _action,
    )

    songs = [SongCandidate(1, "First", "Artist"), SongCandidate(2, "Second", "Artist")]
    provider = _Provider(candidates=songs)
    provider.playlist = AsyncMock(return_value=("Example", songs))
    plugin, pushes, _ = _make_plugin(provider)
    plugin._queues = {}
    await plugin.playlist(
        "search", name="Example", _ctx={**_ctx(), "request_source": "proactive"}
    )
    first = _action(pushes[-1])

    async def deliver(name, ticket, reason):
        await plugin.playlist_terminal(ticket, reason, _ctx=_ctx())

    monkeypatch.setattr(music_playback, "_deliver_queue_terminal", deliver)
    manager = SimpleNamespace(lanlan_name="Mika", enqueue_agent_callback=MagicMock())
    event = dict(
        state="playing",
        track={"name": "First", "artist": "Artist"},
        playback_id="first",
        playback_window_id="owner",
        playback_started_at=100,
        source="proactive",
        netease_id=first["netease_id"],
        queue_ticket=first["queue_ticket"],
    )
    assert music_playback.handle_music_playback_state(manager, event)
    assert manager._current_netease_track["song_id"] == 1
    assert music_playback.handle_music_playback_state(
        manager, {**event, "state": "ended"}
    )
    await asyncio.gather(*music_playback._queue_callbacks)
    second = _action(pushes[-1])
    assert provider.resolve_calls == [1, 2]
    assert second["netease_id"] == 2
    assert second["queue_ticket"] != first["queue_ticket"]

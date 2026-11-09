from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from brain.proactive_music import proactive_music_query, latest_proactive_music_query

ORIGINAL = "去听那首《Keep Me Warm》吧，适合现在的工作节奏。"


@pytest.mark.parametrize(
    "text,query",
    [
        (ORIGINAL, "Keep Me Warm"),
        ("给你推荐一首《晴天》，陪你工作。", "晴天"),
        ("来听听 Chace 的《Keep Me Warm》吧。", "Keep Me Warm"),
        ('Let\'s listen to "Keep Me Warm".', "Keep Me Warm"),
        ("《晴天》的歌词写得很好。", None),
        ("我昨天推荐你听《晴天》。", None),
        ("不要播放《晴天》。", None),
        ("不推荐听《晴天》。", None),
        ("给你推荐《三体》，很有意思。", None),
        ("推荐你听有声书《三体》。", None),
        ("只推荐《晴天》，不播放。", None),
        ("下次给你放首《晴天》。", None),
        ("要不要听听《晴天》？", None),
        ("你刚才说播放《晴天》。", None),
        ("给你推荐《晴天》和《夜曲》。", None),
        ("去听那首《》吧。", None),
    ],
)
def test_explicit_song_offer(text, query):
    assert proactive_music_query(text) == query


def test_only_latest_assistant_is_actionable():
    assert (
        latest_proactive_music_query(
            [
                {"role": "assistant", "content": ORIGINAL},
                {"role": "user", "content": "给我放首《晴天》"},
                {"role": "assistant", "content": "食堂吃饭挺方便的。"},
            ]
        )
        is None
    )


def plugins():
    return [{"id": "netease_music", "entries": [{"id": "play_netease_music"}]}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked", [None, "player_occupied", "music_cooldown", "player_status_unavailable"]
)
async def test_actual_proactive_turn_routes_without_llm(monkeypatch, blocked):
    from brain.task_executor import DirectTaskExecutor
    from brain import proactive_music

    monkeypatch.setattr(
        proactive_music, "proactive_music_block_reason", AsyncMock(return_value=blocked)
    )
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list_provider = AsyncMock(return_value=plugins())
    executor._assess_user_plugin = AsyncMock(
        side_effect=AssertionError("Must not ask user-request-only assessor")
    )
    result = await executor._analyze_and_execute_inner(
        messages=[
            {"role": "user", "content": "刚吃过饭"},
            {"role": "assistant", "content": ORIGINAL},
        ],
        lanlan_name="YUI",
        agent_flags={"user_plugin_enabled": True},
        proactive=True,
    )
    assert result.has_task is (blocked is None)
    if not blocked:
        assert (result.tool_name, result.entry_id, result.tool_args) == (
            "netease_music",
            "play_netease_music",
            {"query": "Keep Me Warm"},
        )
    executor._assess_user_plugin.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog",
    [
        [],
        [
            {
                "id": "netease_music",
                "passive": True,
                "entries": [{"id": "play_netease_music"}],
            }
        ],
    ],
)
async def test_missing_or_passive_music_plugin_never_dispatches(catalog):
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list_provider = AsyncMock(return_value=catalog)
    result = await executor._analyze_and_execute_inner(
        messages=[{"role": "assistant", "content": ORIGINAL}],
        lanlan_name="YUI",
        agent_flags={"user_plugin_enabled": True},
        proactive=True,
    )
    assert not result.has_task


@pytest.mark.asyncio
async def test_plugin_switch_off_blocks_route():
    from brain.task_executor import DirectTaskExecutor

    executor = object.__new__(DirectTaskExecutor)
    result = await executor._analyze_and_execute_inner(
        messages=[{"role": "assistant", "content": ORIGINAL}],
        lanlan_name="YUI",
        agent_flags={"user_plugin_enabled": False},
        proactive=True,
    )
    assert result is None


def test_song_offers_survive_general_cap_but_still_dedupe(monkeypatch):
    from app.agent_server import api_runtime as a

    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    monkeypatch.setattr(a.Modules, "agent_flags", {"user_plugin_enabled": True})
    monkeypatch.setattr(a.Modules, "proactive_analyze_count", {"YUI": 2})
    monkeypatch.setattr(a.Modules, "last_proactive_assistant_fingerprint", {})
    calls = []
    monkeypatch.setattr(
        a, "_background_analyze_and_plan", lambda *args, **kwargs: kwargs
    )
    monkeypatch.setattr(a, "_create_tracked_task", calls.append)
    messages = [{"role": "assistant", "content": ORIGINAL}]
    a._handle_proactive_analyze(messages, "YUI", "YUI", "test")
    a._handle_proactive_analyze(messages, "YUI", "YUI", "test")
    assert len(calls) == 1
    assert a.Modules.proactive_analyze_count["YUI"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,expected",
    [("playing", False), ("paused", False), ("ended", True), ("error", True)],
)
async def test_live_status_blocks_occupied_player(monkeypatch, state, expected):
    from main_routers import music_router, shared_state
    from main_logic.proactive_chat import music_refusal

    manager = SimpleNamespace(
        websocket=object(), _music_playback_event_key=("player", state, 1, False)
    )
    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {"YUI": manager})
    monkeypatch.setattr(
        music_refusal, "is_music_refusal_cooldown_active", lambda _: False
    )
    assert (await music_router.current_netease_track("YUI"))[
        "proactive_allowed"
    ] is expected


@pytest.mark.asyncio
async def test_rechecks_player_before_plugin_http_dispatch(monkeypatch):
    from brain.task_executor import DirectTaskExecutor
    from brain import proactive_music

    monkeypatch.setattr(
        proactive_music,
        "proactive_music_block_reason",
        AsyncMock(return_value="player_occupied"),
    )
    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = plugins()
    result = await executor._execute_user_plugin(
        "test",
        plugin_id="netease_music",
        entry_id="play_netease_music",
        plugin_args={"query": "Keep Me Warm"},
        lanlan_name="YUI",
        reason="proactive_music_recommendation",
    )
    assert result.success is False
    assert result.reason == "player_occupied"

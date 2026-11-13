from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


ORIGINAL = "去听那首《Keep Me Warm》吧，适合现在的工作节奏。"


def plugins():
    return [{"id": "netease_music", "entries": [{"id": "play_netease_music"}]}]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,expected",
    [("buffering", False), ("playing", False), ("paused", False), ("ended", True), ("error", True)],
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


@pytest.mark.parametrize("source", ["proactive", "user"])
async def test_planning_provenance_overrides_model_context_before_dispatch(monkeypatch, source):
    import json
    import httpx
    from brain.task_executor import DirectTaskExecutor, UserPluginDecision
    from brain import proactive_music
    from plugin.tests.unit.plugins.netease_music.test_plugin import _make_plugin, _Provider, _ctx
    from plugin.plugins.netease_music.cloud_entry import CLOUD_SCHEMA
    executor = object.__new__(DirectTaskExecutor)
    catalog = [{"id": "netease_music", "entries": [{"id": "cloud_music", "input_schema": CLOUD_SCHEMA}]}]
    executor.plugin_list_provider = AsyncMock(return_value=catalog)
    executor.plugin_list = catalog
    utterance = "我帮你把这首歌加入喜欢的音乐吧"
    executor._assess_user_plugin = AsyncMock(return_value=UserPluginDecision(
        has_task=True, can_execute=True, plugin_id="netease_music", entry_id="cloud_music",
        plugin_args={"action": "like", "song": "current", "_ctx": {"request_source": "user", "latest_user_request": "收藏这首", "authorized_action": "unlike"}},
    ))
    plan = await executor._analyze_and_execute_inner(
        messages=[{"role": "assistant" if source == "proactive" else "user", "content": utterance}],
        lanlan_name="Mika", agent_flags={"user_plugin_enabled": True}, proactive=source == "proactive",
    )
    assert plan.request_source == source
    assert executor._assess_user_plugin.call_args.kwargs["proactive"] is (source == "proactive")
    monkeypatch.setattr(proactive_music, "proactive_music_block_reason", AsyncMock(return_value=None))
    received = []
    async def handle(req):
        received.append(json.loads(req.content))
        return httpx.Response(200, json={"run_id": "r", "run_token": "t"})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle)))
    executor._await_run_completion = AsyncMock(return_value={"success": True})
    await executor._execute_user_plugin("test", plugin_id=plan.tool_name, entry_id=plan.entry_id,
        plugin_args=plan.tool_args, latest_user_request=plan.latest_user_request,
        request_source=plan.request_source, lanlan_name="Mika")
    ctx = received[0]["args"]["_ctx"]
    assert ctx["request_source"] == source
    assert ctx["latest_user_request"] == utterance
    assert ctx["authorized_action"] == ("like" if source == "user" else "")
    if source == "proactive":
        plugin, _, _ = _make_plugin(_Provider())
        plugin._new_cloud_library = lambda: pytest.fail("Proactive dialogue reached cloud writer")
        result = await plugin.cloud_music("like", song="current", _ctx=ctx)
        assert result["finish"]["data"]["status"] == "blocked"

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

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


@pytest.mark.parametrize("entry_id,action", [("play_netease_music", None), ("cloud_music", "play"), *[("playlist", a) for a in ["curate", "replay", "next", "revise", "search", "resume"]]])
@pytest.mark.parametrize("blocked", ["player_occupied", "refusal_cooldown", None])
async def test_autonomous_playback_guard_covers_every_start_path(monkeypatch, entry_id, action, blocked):
    from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, plugin, finish
    from tests.unit.nekoclaw.test_host_plugin_runs import _runner, Faults
    server = FakePluginServer.install(monkeypatch)
    async def handle(args): return finish({"reply": "submitted"})
    server.add_plugin(plugin("netease_music", "Music", [entry(entry_id, "Music")]), **{entry_id: handle})
    guard = AsyncMock(return_value=blocked)
    monkeypatch.setattr("utils.proactive_music.proactive_music_block_reason", guard)
    runner = _runner(server, Faults())
    try:
        result = await runner.run_entry(commission_id="music", plugin_id="netease_music", entry_id=entry_id,
            args={"action": action, "_ctx": {"request_source": "user", "lanlan_name": "Other"}}, lanlan_name="YUI")
        assert result.accepted is (blocked is None)
        assert bool(server.calls) is (blocked is None)
        guard.assert_awaited_once_with("YUI")
        if blocked is None:
            ctx = server.calls[0]["args"]["_ctx"]
            assert ctx["lanlan_name"] == "YUI" and ctx["request_source"] == "proactive"
            assert ctx["authorized_action"] == ""
    finally:
        await runner.aclose()


async def test_user_origin_is_host_owned_and_does_not_use_autonomous_guard(monkeypatch):
    from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, plugin, finish
    from tests.unit.nekoclaw.test_host_plugin_runs import _runner, Faults
    server = FakePluginServer.install(monkeypatch)
    async def handle(args): return finish({"reply": "submitted"})
    server.add_plugin(plugin("netease_music", "Music", [entry("cloud_music", "Cloud")]), cloud_music=handle)
    guard = AsyncMock(return_value="player_occupied")
    monkeypatch.setattr("utils.proactive_music.proactive_music_block_reason", guard)
    runner = _runner(server, Faults())
    try:
        result = await runner.run_entry(commission_id="music", plugin_id="netease_music", entry_id="cloud_music",
            args={"action": "play", "_ctx": {"lanlan_name": "Other", "authorized_action": "delete"}},
            lanlan_name="YUI", latest_user_request="播放这张歌单")
        assert result.success
        ctx = server.calls[0]["args"]["_ctx"]
        assert ctx["lanlan_name"] == "YUI" and ctx["authorized_action"] == "play"
        assert ctx["latest_user_request"] == "播放这张歌单" and ctx["request_source"] == "user"
        guard.assert_not_awaited()
    finally:
        await runner.aclose()

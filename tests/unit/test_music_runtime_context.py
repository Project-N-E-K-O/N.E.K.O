"""A fulfilled playlist remains known to chat and proactive turns."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

from main_logic.music_context import music_runtime_context, music_runtime_snapshot, music_context_tools
from main_logic.music_messages import music_callback_is_deliverable
from main_logic.music_playback import handle_music_playback_state, clear_music_playback_connection
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text


def manager():
    return SimpleNamespace(lanlan_name="YUI", enqueue_agent_callback=Mock(),
        _music_request={"id": "request-a", "state": "pending",
                        "command": {"action": "cloud_play", "playlist": "liked"}},
        _music_library_pending=True, _music_reply_facts="本轮后台尚未执行。")


def receipt(**changes):
    return {"state": "playing", "playback_id": "track-a", "playback_window_id": "window",
        "playback_started_at": 123.0, "source": "music_play_url", "netease_id": 2065968525,
        "music_request_id": "request-a", "queue_ticket": "a" * 32,
        "track": {"name": "温室暴风雨", "artist": "张婉清"}, **changes}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr("main_logic.music_playback._deliver_queue_terminal", AsyncMock())
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", lambda: None)


@pytest.mark.asyncio
async def test_pending_fact_prefix_can_be_sent_without_entering_saved_history():
    client = _client()
    client.script = [[_text("我去处理。"), _text("", "stop")]]
    await client.stream_text("放吧", system_prefix="======[音乐事实]======本轮后台尚未执行。",
                             system_prefix_history="")
    assert "后台尚未执行" in str(client.requests[-1])
    assert "后台尚未执行" not in str(client._conversation_history)


def test_offline_factory_binds_dynamic_context_for_start_and_handoff(monkeypatch):
    from main_logic.core import lifecycle
    factory = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(lifecycle, "OmniOfflineClient", factory)
    m = Mock(lanlan_name="YUI", master_name="User", user_language="zh-CN")
    m._music_request = manager()._music_request
    m._music_confirmed_state = {"state": "playing", "name": "Song"}
    config = {"api_key": "test", "base_url": "http://example.test", "model": "test"}
    session = lifecycle.LifecycleMixin._create_offline_vlm_client(m, conversation_config=config,
        vision_config=config, tool_definitions=[], max_response_length=100, external_tts_enabled=True)
    assert '"playback_state": "playing"' in session.context_tools_provider()[0].read()['observation']
    m._music_confirmed_state["state"] = "error"
    assert '"playback_state": "error"' in session.context_tools_provider()[0].read()['observation']
    assert 'CURRENT topic' in session.request_policy_provider()
    assert '用户说中文就用中文' in session.request_policy_provider()
    m.user_language = 'zh-TW'
    assert '使用者說中文就用中文' in session.request_policy_provider()
    m.user_language = 'en'
    assert 'reply in the language of their current message' in session.request_policy_provider()
    assert 'pending' not in session.request_policy_provider()
    assert 'failed' not in session.request_policy_provider()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("proactive", [False, True])
async def test_player_details_are_read_on_demand_and_expire(provider, proactive, monkeypatch):
    from tests.unit.test_offline_turn_cancellation_e2e import _tool_calls, _gemini_calls
    from tests.unit.test_turn_context_request_view import _view, _saved, _text_step
    import main_logic.omni_offline_client._genai_support as genai
    monkeypatch.setattr(genai, "_GENAI_AVAILABLE", True)
    m = manager()
    handle_music_playback_state(m, receipt())
    client = _client(provider)
    client.context_tools_provider = lambda: music_context_tools(m)
    from config.prompts.prompts_music_context import MUSIC_CONVERSATION_RULES
    client.request_policy_provider = lambda: MUSIC_CONVERSATION_RULES
    call = _tool_calls("player") if provider == "openai" else _gemini_calls("player")
    if provider == "openai":
        call.tool_call_deltas[0]["function"]["name"] = "read_music_state"
    else:
        call.candidates[0].content.parts[0].function_call.name = "read_music_state"
    client.script = [[call], _text_step(provider, "播放成功。"), _text_step(provider, "好。")]
    if proactive:
        await client.prompt_ephemeral("确认刚才的点歌结果。")
    else:
        await client.stream_text("放上了吗？")
    first = str(_view(client.requests[0], provider))
    second = str(_view(client.requests[1], provider))
    if provider == "gemini":
        second += str([part.function_response.response for content in client.requests[1]
                       for part in content.parts if getattr(part, "function_response", None)])
    assert "温室暴风雨" not in first and "matched_player" not in first
    if provider == "openai":  # Gemini extracts system policy out of contents.
        policy_texts = [text for _role, text, _calls in _view(client.requests[0], provider)]
        assert "CURRENT topic" in policy_texts[-1]
    assert "温室暴风雨" in second and "playback_state" in second
    assert "温室暴风雨" not in _saved(client)
    await client.stream_text("晚饭吃什么？")
    assert "温室暴风雨" not in str(_view(client.requests[-1], provider))
    m._music_confirmed_state["state"] = "paused"
    assert '"playback_state": "paused"' in client._context_tools()[0].read()["observation"]
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_passive_player_receipts_cannot_prime_unrelated_chat_but_errors_and_first_play_survive():
    from main_logic.core import LLMSessionManager
    from tests.unit.test_core_game_route_memory_contract import (
        _make_callback_media_manager, _make_offline_session_for_callback_media,
    )
    m = _make_callback_media_manager(_make_offline_session_for_callback_media())
    m._music_request = manager()._music_request
    callbacks = []
    source = manager()
    for state in ["playing", "paused", "ended", "buffering", "error"]:
        event = receipt(state=state, playback_id=state, playback_started_at=123 + len(callbacks))
        handle_music_playback_state(source, event)
        callback = deepcopy(source.enqueue_agent_callback.call_args.args[0])
        callback["delivery_mode"] = "passive"
        callbacks.append(callback)
    first_play = deepcopy(callbacks[0])
    first_play["delivery_mode"] = "proactive"
    callbacks.append(first_play)
    m.pending_agent_callbacks = callbacks[:]
    result = LLMSessionManager.filter_deliverable_callbacks(m, callbacks)
    assert result == [callbacks[4], first_play]
    assert m.pending_agent_callbacks == [callbacks[4], first_play]
    assert music_runtime_snapshot(source)["playback_state"] == "error"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_real_receipt_without_request_link_does_not_claim_failure_or_fulfillment():
    m = SimpleNamespace(lanlan_name="YUI", enqueue_agent_callback=Mock())
    handle_music_playback_state(m, receipt())
    snapshot = music_runtime_snapshot(m)
    assert snapshot["playback_state"] == "playing"
    assert snapshot["track"]["name"] == "温室暴风雨"
    assert "request" not in snapshot
    assert "fulfilled" not in snapshot
    assert "matched_player" not in music_runtime_context(m)
    await asyncio.sleep(0)

from unittest.mock import Mock

import pytest

from main_logic.core import LLMSessionManager
from main_routers import game_router


class _AsyncNullLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeResampler:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


class _FakeState:
    def __init__(self):
        self.preempt_marked = False
        self.events = []

    def mark_user_input_preempt(self):
        self.preempt_marked = True

    async def fire(self, event, **kwargs):
        self.events.append((event, kwargs))


class _FakeQueue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


class _FakeActivityTracker:
    def __init__(self):
        self.voice_rms_count = 0
        self.user_messages = []

    def on_voice_rms(self):
        self.voice_rms_count += 1

    def on_user_message(self, text):
        self.user_messages.append(text)


def _make_manager():
    mgr = object.__new__(LLMSessionManager)
    mgr.websocket = None
    mgr.sync_message_queue = _FakeQueue()
    mgr.lanlan_name = "Lan"
    mgr.lock = _AsyncNullLock()
    mgr.audio_resampler = _FakeResampler()
    mgr.use_tts = False
    mgr.current_speech_id = "old-speech"
    mgr._tts_done_queued_for_turn = False
    mgr._tts_done_pending_until_ready = False
    mgr.state = _FakeState()
    mgr._active_text_request_id = None
    mgr._pending_turn_meta = None
    mgr._current_ai_turn_text = ""
    mgr.tts_ready = False
    mgr.tts_thread = None
    mgr.tts_pending_chunks = []
    mgr.tts_cache_lock = _AsyncNullLock()
    mgr.sent_responses = []
    mgr.user_activity = []

    async def send_user_activity(interrupted_speech_id):
        mgr.user_activity.append(interrupted_speech_id)

    async def send_lanlan_response(text, is_first_chunk=False, turn_id=None, metadata=None, **_kwargs):
        mgr.sent_responses.append({
            "text": text,
            "is_first_chunk": is_first_chunk,
            "turn_id": turn_id,
            "metadata": metadata,
            "request_id": _kwargs.get("request_id"),
        })

    async def ensure_game_tts_runtime():
        return False

    mgr.send_user_activity = send_user_activity
    mgr.send_lanlan_response = send_lanlan_response
    mgr._ensure_game_tts_runtime = ensure_game_tts_runtime
    return mgr


def _make_transcript_manager():
    mgr = _make_manager()
    mgr.session = object()
    mgr._activity_tracker = _FakeActivityTracker()
    mgr._session_turn_count = 0
    mgr._publish_user_utterance_to_plugin_bus = Mock()
    return mgr


@pytest.mark.unit
@pytest.mark.asyncio
async def test_speak_game_line_text_mirror_carries_game_route_metadata():
    mgr = _make_manager()

    result = await LLMSessionManager.speak_game_line(
        mgr,
        "看我这一脚",
        request_id="req-1",
        game_type="soccer",
        session_id="match_1",
        event={
            "kind": "opening-line",
            "hasUserSpeech": False,
            "hasUserText": False,
        },
    )

    assert result["ok"] is True
    assert result["turn_end_emitted"] is True
    assert result["interrupt_audio"] is False
    assert mgr.user_activity == []
    assert mgr.audio_resampler.cleared is False
    assert mgr.sent_responses[0]["request_id"] == "req-1"
    assert mgr.sent_responses[0]["metadata"] == {
        "source": "game_route",
        "game_type": "soccer",
        "session_id": "match_1",
        "game_route": {
            "game_type": "soccer",
            "session_id": "match_1",
            "event": {
                "kind": "opening-line",
                "hasUserSpeech": False,
                "hasUserText": False,
            },
        },
    }
    assert mgr.sync_message_queue.messages == [{
        "type": "system",
        "data": "turn end",
        "request_id": "req-1",
        "meta": mgr.sent_responses[0]["metadata"],
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_speak_game_line_can_leave_turn_end_to_text_mirror():
    mgr = _make_manager()

    result = await LLMSessionManager.speak_game_line(
        mgr,
        "只播放语音",
        request_id="req-voice",
        game_type="soccer",
        session_id="match_1",
        mirror_text=False,
        emit_turn_end=False,
        event={"kind": "user-text", "hasUserText": True},
    )

    assert result["ok"] is True
    assert result["turn_end_emitted"] is False
    assert result["interrupt_audio"] is False
    assert mgr.user_activity == []
    assert mgr.audio_resampler.cleared is False
    assert mgr.sent_responses == []
    assert mgr.sync_message_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_speak_game_line_interrupt_audio_triggers_existing_interrupt_path():
    mgr = _make_manager()

    result = await LLMSessionManager.speak_game_line(
        mgr,
        "先听我说完",
        request_id="req-interrupt",
        game_type="soccer",
        session_id="match_1",
        mirror_text=False,
        emit_turn_end=False,
        interrupt_audio=True,
        event={"kind": "user-text", "hasUserText": True},
    )

    assert result["ok"] is True
    assert result["interrupt_audio"] is True
    assert mgr.user_activity == ["old-speech"]
    assert mgr.audio_resampler.cleared is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_game_assistant_text_can_finalize_user_reply_turn():
    mgr = _make_manager()

    result = await LLMSessionManager.mirror_game_assistant_text(
        mgr,
        "听见啦，我会放慢一点。",
        request_id="req-user",
        game_type="soccer",
        session_id="match_1",
        source="game-llm-result",
        turn_id="turn-user",
        event={"kind": "user-text", "hasUserText": True},
        finalize_turn=True,
    )

    assert result["ok"] is True
    assert result["turn_finalized"] is True
    assert mgr.sent_responses[0]["request_id"] == "req-user"
    assert mgr.sent_responses[0]["metadata"]["game_route"]["event"] == {
        "kind": "user-text",
        "hasUserText": True,
    }
    assert mgr.sync_message_queue.messages == [{
        "type": "system",
        "data": "turn end",
        "request_id": "req-user",
        "meta": {
            "source": "game_route",
            "game_type": "soccer",
            "session_id": "match_1",
            "game_route": {
                "game_type": "soccer",
                "session_id": "match_1",
                "event": {"kind": "user-text", "hasUserText": True},
            },
        },
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_route_voice_transcript_handled_skips_ordinary_user_context(monkeypatch):
    mgr = _make_transcript_manager()
    routed = []

    async def fake_route(lanlan_name, text, *, request_id, game_type=None, session_id=None):
        routed.append((lanlan_name, text, request_id, game_type, session_id))
        return True

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: True)
    monkeypatch.setattr(game_router, "_get_active_game_route_state", lambda lanlan_name: {
        "game_type": "soccer",
        "session_id": "match_1",
    })
    monkeypatch.setattr(game_router, "route_external_voice_transcript", fake_route)

    await LLMSessionManager.handle_input_transcript(mgr, "  我要射门了  ", is_voice_source=True)

    assert routed and routed[0][0] == "Lan"
    assert routed[0][1] == "我要射门了"
    assert routed[0][2].startswith("realtime-stt-")
    assert routed[0][3] == "soccer"
    assert routed[0][4] == "match_1"
    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == []
    assert mgr._session_turn_count == 0
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inactive_voice_transcript_uses_ordinary_flow(monkeypatch):
    mgr = _make_transcript_manager()

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: False)

    await LLMSessionManager.handle_input_transcript(mgr, "  普通语音  ", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["  普通语音  "]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "  普通语音  ",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "普通语音"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_voice_transcript_reuse_keeps_existing_ordinary_flow(monkeypatch):
    mgr = _make_transcript_manager()

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: False)

    await LLMSessionManager.handle_input_transcript(mgr, "文本复用", is_voice_source=False)

    assert mgr._activity_tracker.voice_rms_count == 0
    assert mgr._activity_tracker.user_messages == []
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "文本复用"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_voice_transcript_reuse_does_not_enter_active_game_route(monkeypatch):
    mgr = _make_transcript_manager()

    async def fail_route(*_args, **_kwargs):
        raise AssertionError("non-voice transcript reuse must not route through game voice")

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: True)
    monkeypatch.setattr(game_router, "route_external_voice_transcript", fail_route)

    await LLMSessionManager.handle_input_transcript(mgr, "文本复用", is_voice_source=False)

    assert mgr._activity_tracker.voice_rms_count == 0
    assert mgr._activity_tracker.user_messages == []
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "文本复用"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("route_outcome", ["false", "exception"])
async def test_game_route_voice_transcript_falls_back_when_unhandled(monkeypatch, route_outcome):
    mgr = _make_transcript_manager()

    async def fake_route(_lanlan_name, _text, *, request_id, game_type=None, session_id=None):
        assert request_id.startswith("realtime-stt-")
        assert game_type == "soccer"
        assert session_id == "match_1"
        if route_outcome == "exception":
            raise RuntimeError("route failed")
        return False

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: True)
    monkeypatch.setattr(game_router, "_get_active_game_route_state", lambda lanlan_name: {
        "game_type": "soccer",
        "session_id": "match_1",
    })
    monkeypatch.setattr(game_router, "route_external_voice_transcript", fake_route)

    await LLMSessionManager.handle_input_transcript(mgr, "继续普通流程", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["继续普通流程"]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "继续普通流程",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "继续普通流程"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_route_response_complete_clears_interrupted_ordinary_turn(monkeypatch):
    mgr = _make_manager()
    mgr._active_text_request_id = "req-old"
    mgr._pending_turn_meta = {"source": "ordinary"}
    mgr._current_ai_turn_text = "ordinary text before game"
    mgr.tts_pending_chunks = [("sid-old", "queued text")]

    monkeypatch.setattr(LLMSessionManager, "_is_game_route_active", lambda self: True)

    await LLMSessionManager.handle_response_complete(mgr)

    assert mgr._active_text_request_id is None
    assert mgr._pending_turn_meta is None
    assert mgr._current_ai_turn_text == ""
    assert mgr.tts_pending_chunks == []
    assert mgr.sync_message_queue.messages == []

import pytest

from main_logic.core import LLMSessionManager


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
    mgr.tts_ready = False
    mgr.tts_thread = None
    mgr.tts_pending_chunks = []
    mgr.tts_cache_lock = _AsyncNullLock()
    mgr.sent_responses = []
    mgr.user_activity = []

    async def send_user_activity(interrupted_speech_id):
        mgr.user_activity.append(interrupted_speech_id)

    async def send_lanlan_response(text, is_first_chunk=False, turn_id=None, metadata=None):
        mgr.sent_responses.append({
            "text": text,
            "is_first_chunk": is_first_chunk,
            "turn_id": turn_id,
            "metadata": metadata,
        })

    async def ensure_game_tts_runtime():
        return False

    mgr.send_user_activity = send_user_activity
    mgr.send_lanlan_response = send_lanlan_response
    mgr._ensure_game_tts_runtime = ensure_game_tts_runtime
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

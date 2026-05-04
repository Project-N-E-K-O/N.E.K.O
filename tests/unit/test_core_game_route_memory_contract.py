from unittest.mock import Mock

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
    mgr.tts_handler_task = None
    mgr._takeover_active = False
    mgr._takeover_input_dispatcher = None
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

    async def ensure_tts_pipeline_alive():
        return None

    mgr.send_user_activity = send_user_activity
    mgr.send_lanlan_response = send_lanlan_response
    mgr.ensure_tts_pipeline_alive = ensure_tts_pipeline_alive
    return mgr


def _make_transcript_manager():
    mgr = _make_manager()
    mgr.session = object()
    mgr._activity_tracker = _FakeActivityTracker()
    mgr._session_turn_count = 0
    mgr._publish_user_utterance_to_plugin_bus = Mock()
    return mgr


def _soccer_mirror_meta(event):
    return {
        "source": "game_route",
        "kind": "soccer",
        "session_id": "match_1",
        "mirror": {
            "kind": "soccer",
            "session_id": "match_1",
            "event": event,
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_text_mirror_carries_metadata():
    mgr = _make_manager()
    event = {
        "kind": "opening-line",
        "hasUserSpeech": False,
        "hasUserText": False,
    }
    metadata = _soccer_mirror_meta(event)

    result = await LLMSessionManager.mirror_assistant_speech(
        mgr,
        "看我这一脚",
        metadata=metadata,
        request_id="req-1",
    )

    assert result["ok"] is True
    assert result["turn_end_emitted"] is True
    assert result["interrupt_audio"] is False
    assert mgr.user_activity == []
    assert mgr.audio_resampler.cleared is False
    assert mgr.sent_responses[0]["request_id"] == "req-1"
    assert mgr.sent_responses[0]["metadata"] == metadata
    assert mgr.sync_message_queue.messages == [{
        "type": "system",
        "data": "turn end",
        "request_id": "req-1",
        "meta": metadata,
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_can_leave_turn_end_to_text_mirror():
    mgr = _make_manager()

    result = await LLMSessionManager.mirror_assistant_speech(
        mgr,
        "只播放语音",
        metadata=_soccer_mirror_meta({"kind": "user-text", "hasUserText": True}),
        request_id="req-voice",
        mirror_text=False,
        emit_turn_end_after=False,
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
async def test_mirror_assistant_speech_interrupt_audio_triggers_existing_interrupt_path():
    mgr = _make_manager()

    result = await LLMSessionManager.mirror_assistant_speech(
        mgr,
        "先听我说完",
        metadata=_soccer_mirror_meta({"kind": "user-text", "hasUserText": True}),
        request_id="req-interrupt",
        mirror_text=False,
        emit_turn_end_after=False,
        interrupt_audio=True,
    )

    assert result["ok"] is True
    assert result["interrupt_audio"] is True
    assert mgr.user_activity == ["old-speech"]
    assert mgr.audio_resampler.cleared is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_output_can_finalize_user_reply_turn():
    mgr = _make_manager()
    event = {"kind": "user-text", "hasUserText": True}
    metadata = _soccer_mirror_meta(event)

    result = await LLMSessionManager.mirror_assistant_output(
        mgr,
        "听见啦，我会放慢一点。",
        metadata=metadata,
        request_id="req-user",
        turn_id="turn-user",
        finalize_turn=True,
    )

    assert result["ok"] is True
    assert result["turn_finalized"] is True
    assert mgr.sent_responses[0]["request_id"] == "req-user"
    assert mgr.sent_responses[0]["metadata"]["mirror"]["event"] == event
    assert mgr.sync_message_queue.messages == [{
        "type": "system",
        "data": "turn end",
        "request_id": "req-user",
        "meta": metadata,
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_takeover_dispatcher_handles_voice_transcript_and_skips_ordinary_user_context():
    mgr = _make_transcript_manager()
    routed = []

    async def fake_dispatcher(lanlan_name, text, *, request_id):
        routed.append((lanlan_name, text, request_id))
        return True

    mgr._takeover_active = True
    mgr._takeover_input_dispatcher = fake_dispatcher

    await LLMSessionManager.handle_input_transcript(mgr, "  我要射门了  ", is_voice_source=True)

    assert routed and routed[0][0] == "Lan"
    assert routed[0][1] == "我要射门了"
    assert routed[0][2].startswith("realtime-stt-")
    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == []
    assert mgr._session_turn_count == 0
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_takeover_voice_transcript_uses_ordinary_flow():
    mgr = _make_transcript_manager()

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
async def test_no_takeover_non_voice_transcript_reuse_keeps_existing_ordinary_flow():
    mgr = _make_transcript_manager()

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
async def test_takeover_dispatcher_does_not_intercept_non_voice_transcript_reuse():
    mgr = _make_transcript_manager()

    async def fail_dispatcher(*_args, **_kwargs):
        raise AssertionError("non-voice transcript reuse must not route through takeover dispatcher")

    mgr._takeover_active = True
    mgr._takeover_input_dispatcher = fail_dispatcher

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
@pytest.mark.parametrize("dispatcher_outcome", ["false", "exception"])
async def test_takeover_dispatcher_falls_back_when_unhandled(dispatcher_outcome):
    mgr = _make_transcript_manager()

    async def fake_dispatcher(_lanlan_name, _text, *, request_id):
        assert request_id.startswith("realtime-stt-")
        if dispatcher_outcome == "exception":
            raise RuntimeError("dispatcher failed")
        return False

    mgr._takeover_active = True
    mgr._takeover_input_dispatcher = fake_dispatcher

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
async def test_takeover_response_complete_clears_interrupted_ordinary_turn():
    mgr = _make_manager()
    mgr._active_text_request_id = "req-old"
    mgr._pending_turn_meta = {"source": "ordinary"}
    mgr._current_ai_turn_text = "ordinary text before takeover"
    mgr.tts_pending_chunks = [("sid-old", "queued text")]
    mgr._takeover_active = True

    await LLMSessionManager.handle_response_complete(mgr)

    assert mgr._active_text_request_id is None
    assert mgr._pending_turn_meta is None
    assert mgr._current_ai_turn_text == ""
    assert mgr.tts_pending_chunks == []
    assert mgr.sync_message_queue.messages == []

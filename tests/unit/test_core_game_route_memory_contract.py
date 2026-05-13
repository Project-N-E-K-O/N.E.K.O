import asyncio
from collections import deque
import queue
from unittest.mock import Mock

import pytest

import main_logic.core as core_module


FIXED_TS = 1_700_000_000.0


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

    def empty(self):
        return not self.messages

    def get_nowait(self):
        if not self.messages:
            raise queue.Empty
        return self.messages.pop(0)


class _FakeActivityTracker:
    def __init__(self):
        self.voice_rms_count = 0
        self.user_messages = []

    def on_voice_rms(self):
        self.voice_rms_count += 1

    def on_user_message(self, text):
        self.user_messages.append(text)


class _FakeAliveThread:
    def is_alive(self):
        return True


def _make_manager():
    mgr = object.__new__(core_module.LLMSessionManager)
    mgr.websocket = None
    mgr.websocket_lock = None
    mgr.session = None
    mgr.sync_message_queue = _FakeQueue()
    mgr.lanlan_name = "Lan"
    mgr.master_name = "Master"
    mgr.emotion_pattern = core_module.re.compile("<(.*?)>")
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
    mgr._recent_ai_voice_echo_text = ""
    mgr._recent_ai_voice_echo_at = 0.0
    mgr._pending_ai_voice_echo_text = ""
    mgr._pending_ai_voice_echo_chunks = deque()
    mgr._confirmed_ai_voice_echo_audio_speech_ids = set()
    mgr.tts_ready = False
    mgr.tts_thread = None
    mgr.tts_request_queue = _FakeQueue()
    mgr.tts_response_queue = _FakeQueue()
    mgr.tts_pending_chunks = []
    mgr.tts_cache_lock = _AsyncNullLock()
    mgr._tts_stream_normalizer = core_module.TtsStreamNormalizer()
    mgr._tts_markdown_stripper = core_module.TtsMarkdownStripper()
    mgr._tts_bracket_stripper = core_module.TtsBracketStripper()
    mgr._tts_norm_speech_id = None
    mgr._tts_normalize_enabled = False
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

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
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

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
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

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
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

    result = await core_module.LLMSessionManager.mirror_assistant_output(
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

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "  我要射门了  ", is_voice_source=True)

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
async def test_takeover_dispatcher_receives_voice_echo_match_before_suppression(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "开始比赛吧朋友"
    mgr._recent_ai_voice_echo_at = FIXED_TS
    routed = []

    async def fake_dispatcher(lanlan_name, text, *, request_id):
        routed.append((lanlan_name, text, request_id))
        return True

    mgr._takeover_active = True
    mgr._takeover_input_dispatcher = fake_dispatcher

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "开始比赛吧朋友",
        is_voice_source=True,
    )

    assert routed and routed[0][1] == "开始比赛吧朋友"
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

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "  普通语音  ", is_voice_source=True)

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
async def test_likely_ai_echo_voice_transcript_is_suppressed(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "刚才我主动说了一句：要不要休息一下喝点水。"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "要不要休息一下喝点水", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 0
    assert mgr._activity_tracker.user_messages == []
    assert mgr._session_turn_count == 0
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_echo_voice_transcript_switch_can_disable_suppression(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", False)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "刚才我主动说了一句：要不要休息一下喝点水。"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "要不要休息一下喝点水", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["要不要休息一下喝点水"]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "要不要休息一下喝点水",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "要不要休息一下喝点水"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_ai_echo_voice_transcript_is_not_suppressed(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "刚才我主动说了一句：要不要休息一下喝点水。"
    mgr._recent_ai_voice_echo_at = FIXED_TS - 25

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "要不要休息一下喝点水", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["要不要休息一下喝点水"]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "要不要休息一下喝点水",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "要不要休息一下喝点水"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_barge_in_different_from_recent_ai_text_is_not_suppressed(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "刚才我主动说了一句：要不要休息一下喝点水。"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "先别休息帮我打开设置", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["先别休息帮我打开设置"]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "先别休息帮我打开设置",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "先别休息帮我打开设置"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_short_keyword_barge_in_from_recent_ai_text_is_not_suppressed(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "Do you want tea or coffee?"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "coffee", is_voice_source=True)

    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["coffee"]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "coffee",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "coffee"},
    }]


@pytest.mark.unit
def test_voice_echo_suppression_cache_reset_clears_cross_session_state():
    mgr = _make_transcript_manager()
    mgr._recent_ai_voice_echo_text = "刚才我主动说了一句：要不要休息一下喝点水。"
    mgr._recent_ai_voice_echo_at = FIXED_TS
    mgr._pending_ai_voice_echo_text = "还没确认播放的文本"
    mgr._pending_ai_voice_echo_chunks.append(("old-speech", "还没确认播放的文本"))
    mgr._confirmed_ai_voice_echo_audio_speech_ids.add("old-speech")

    core_module.LLMSessionManager._reset_voice_echo_suppression_cache(mgr)

    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0
    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_defaults_to_skip_display_echo_cache(monkeypatch):
    mgr = _make_manager()
    mgr.use_tts = True
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    await core_module.LLMSessionManager.send_lanlan_response(mgr, "显示文本（括号也显示）")

    assert mgr._current_ai_turn_text == "显示文本（括号也显示）"
    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_can_explicitly_remember_voice_echo_with_tts(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.use_tts = True

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "确认已经播报的文本",
        remember_voice_echo=True,
    )

    assert mgr._recent_ai_voice_echo_text == "确认已经播报的文本"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_confirms_audio_echo_after_tts_audio(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr.tts_request_queue = _FakeQueue()
    mgr._tts_stream_normalizer = core_module.TtsStreamNormalizer()
    mgr._tts_markdown_stripper = core_module.TtsMarkdownStripper()
    mgr._tts_bracket_stripper = core_module.TtsBracketStripper()
    mgr._tts_norm_speech_id = None
    mgr._tts_normalize_enabled = False

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "要不要休息一下（这句不会念）喝点水",
        metadata=_soccer_mirror_meta({"kind": "opening-line"}),
        request_id="req-mirror-voice",
        mirror_text=False,
        emit_turn_end_after=False,
    )

    assert result["audio_queued"] is True
    speech_id = mgr.tts_request_queue.messages[0][0]
    assert mgr.tts_request_queue.messages[0][1] == "要不要休息一下喝点水"
    assert mgr._pending_ai_voice_echo_text == "要不要休息一下喝点水"
    assert list(mgr._pending_ai_voice_echo_chunks) == [(speech_id, "要不要休息一下喝点水")]
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()
    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, speech_id)

    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == {speech_id}
    assert mgr._recent_ai_voice_echo_text == "要不要休息一下喝点水"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS


@pytest.mark.unit
def test_confirm_pending_ai_voice_echo_promotes_only_next_played_chunk(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "已经发出音频的第一句")
    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "还在队列里的第二句")

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, "speech-1")

    assert mgr._recent_ai_voice_echo_text == "已经发出音频的第一句"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS
    assert mgr._pending_ai_voice_echo_text == "还在队列里的第二句"
    assert list(mgr._pending_ai_voice_echo_chunks) == [("speech-1", "还在队列里的第二句")]


@pytest.mark.unit
def test_confirm_pending_ai_voice_echo_skips_sidless_confirmation(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "无法确认归属的文本")

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr)

    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0
    assert mgr._pending_ai_voice_echo_text == "无法确认归属的文本"
    assert list(mgr._pending_ai_voice_echo_chunks) == [("speech-1", "无法确认归属的文本")]
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()


@pytest.mark.unit
def test_confirm_pending_ai_voice_echo_promotes_once_per_speech_id(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "第一段文本")
    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "第二段未播文本")

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, "speech-1")
    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, "speech-1")

    assert mgr._recent_ai_voice_echo_text == "第一段文本"
    assert mgr._pending_ai_voice_echo_text == "第二段未播文本"
    assert list(mgr._pending_ai_voice_echo_chunks) == [("speech-1", "第二段未播文本")]


@pytest.mark.unit
def test_confirm_pending_ai_voice_echo_ignores_late_old_speech_id_for_new_pending(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "new-speech", "new turn pending text")

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, "old-speech")

    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0
    assert mgr._pending_ai_voice_echo_text == "new turn pending text"
    assert list(mgr._pending_ai_voice_echo_chunks) == [("new-speech", "new turn pending text")]
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()

    core_module.LLMSessionManager._confirm_pending_ai_voice_echo(mgr, "new-speech")

    assert mgr._recent_ai_voice_echo_text == "new turn pending text"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS
    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == {"new-speech"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_first_chunk_drops_stale_pending_echo_before_new_tts(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.use_tts = True
    mgr.tts_ready = True
    mgr.tts_thread = _FakeAliveThread()
    mgr.current_speech_id = "new-speech"
    mgr.tts_pending_chunks = [("old-speech", "old cached text")]
    mgr.tts_response_queue.put(("__audio__", "old-speech", b"old-audio"))

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "old-speech", "old unplayed text")
    mgr._confirmed_ai_voice_echo_audio_speech_ids.add("old-speech")

    await core_module.LLMSessionManager.handle_text_data(
        mgr,
        "new tts text",
        is_first_chunk=True,
    )

    assert mgr.tts_response_queue.empty()
    assert mgr.tts_pending_chunks == []
    assert mgr.tts_request_queue.messages == [("new-speech", "new tts text")]
    assert mgr._pending_ai_voice_echo_text == "new tts text"
    assert list(mgr._pending_ai_voice_echo_chunks) == [("new-speech", "new tts text")]
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()
    assert mgr._recent_ai_voice_echo_text == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sidless_tts_audio_discards_pending_echo(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.tts_response_queue = queue.Queue()
    mgr.tts_response_queue.put(b"sidless-audio")
    mgr.current_speech_id = "new-turn"
    send_called = asyncio.Event()

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "new-turn", "new turn pending text")

    async def send_speech(audio, speech_id=None):
        assert audio == b"sidless-audio"
        assert speech_id is None
        send_called.set()
        return True

    monkeypatch.setattr(mgr, "send_speech", send_speech)

    task = asyncio.create_task(core_module.LLMSessionManager.tts_response_handler(mgr))
    await asyncio.wait_for(send_called.wait(), timeout=1)
    task.cancel()
    cancelled_result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(cancelled_result[0], asyncio.CancelledError)

    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_tts_audio_send_drops_unplayed_pending_echo(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.tts_response_queue = queue.Queue()
    mgr.tts_response_queue.put(("__audio__", "speech-1", b"failed-audio"))
    send_called = asyncio.Event()

    core_module.LLMSessionManager._remember_pending_ai_voice_echo(mgr, "speech-1", "unplayed pending text")

    async def send_speech(audio, speech_id=None):
        assert audio == b"failed-audio"
        assert speech_id == "speech-1"
        send_called.set()
        return False

    monkeypatch.setattr(mgr, "send_speech", send_speech)

    task = asyncio.create_task(core_module.LLMSessionManager.tts_response_handler(mgr))
    await asyncio.wait_for(send_called.wait(), timeout=1)
    task.cancel()
    cancelled_result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(cancelled_result[0], asyncio.CancelledError)

    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_tts_pipeline_drops_only_unplayed_echo_cache(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr.tts_thread = _FakeAliveThread()
    mgr._recent_ai_voice_echo_text = "已经播出的尾音"
    mgr._recent_ai_voice_echo_at = FIXED_TS
    mgr._pending_ai_voice_echo_text = "还没来得及播放的队列文本"
    mgr._pending_ai_voice_echo_chunks.append(("old-speech", "还没来得及播放的队列文本"))
    mgr._confirmed_ai_voice_echo_audio_speech_ids.add("old-speech")
    mgr.tts_pending_chunks = [("sid-old", "pending text")]

    await core_module.LLMSessionManager._clear_tts_pipeline(mgr)

    assert mgr.tts_request_queue.messages == [("__interrupt__", None)]
    assert mgr.tts_pending_chunks == []
    assert mgr._pending_ai_voice_echo_text == ""
    assert list(mgr._pending_ai_voice_echo_chunks) == []
    assert mgr._confirmed_ai_voice_echo_audio_speech_ids == set()
    assert mgr._recent_ai_voice_echo_text == "已经播出的尾音"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_takeover_non_voice_transcript_reuse_keeps_existing_ordinary_flow():
    mgr = _make_transcript_manager()

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "文本复用", is_voice_source=False)

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

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "文本复用", is_voice_source=False)

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

    await core_module.LLMSessionManager.handle_input_transcript(mgr, "继续普通流程", is_voice_source=True)

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

    await core_module.LLMSessionManager.handle_response_complete(mgr)

    assert mgr._active_text_request_id is None
    assert mgr._pending_turn_meta is None
    assert mgr._current_ai_turn_text == ""
    assert mgr.tts_pending_chunks == []
    assert mgr.sync_message_queue.messages == []

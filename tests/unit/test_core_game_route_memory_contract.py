import asyncio
from collections import OrderedDict, deque
import queue
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import main_logic.cross_server as cross_server_module
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
        self.mode = core_module.CognitionMode.REGULAR

    def mark_user_input_preempt(self):
        self.preempt_marked = True

    async def fire(self, event, **kwargs):
        self.events.append((event, kwargs))

    async def update_focus(self, *_args, **_kwargs):
        self.mode = core_module.CognitionMode.REGULAR
        return self.mode

    async def clear_focus(self):
        self.mode = core_module.CognitionMode.REGULAR

    def snapshot(self):
        return {
            "focus_charge": 0.0,
            "focus_charge_at": 0.0,
            "focus_episode_id": None,
        }


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


class _ConnectedClientState:
    CONNECTED = "connected"

    def __eq__(self, other):
        return other == self.CONNECTED


class _FakeConnectedWebSocket:
    def __init__(self):
        self.client_state = _ConnectedClientState()
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _FakeActivityTracker:
    def __init__(self):
        self.voice_rms_count = 0
        self.user_messages = []
        self.ai_messages = []

    def on_voice_rms(self):
        self.voice_rms_count += 1

    def on_user_message(self, text):
        self.user_messages.append(text)

    def on_ai_message(self, text=None, now=None):
        self.ai_messages.append((text, now))


class _FakeVoiceBridgeSession:
    def __init__(self):
        self.cancelled = 0
        self.primed = []

    async def cancel_response(self):
        self.cancelled += 1

    async def prime_context(self, context, *, skipped=False):
        self.primed.append((context, skipped))


class _FakeGeminiVoiceBridgeSession(core_module.OmniRealtimeClient):
    def __init__(self):
        self._is_gemini = True
        self.primed = []

    async def prime_context(self, context, *, skipped=False):
        self.primed.append((context, skipped))


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
    mgr._magic_command_image_drop_request_ids = set()
    mgr._magic_command_image_drop_request_order = deque()
    mgr._pending_turn_meta = None
    mgr._current_ai_turn_text = ""
    mgr._focus_indicator_active = False
    mgr._focus_thinking_active = False
    mgr._focus_artifacts_pending = False
    mgr._focus_artifacts_history_start = None
    mgr._focus_emotion_reading = None
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
    mgr._bg_tasks = set()
    mgr.sent_responses = []
    mgr.user_activity = []
    mgr.last_user_activity_time = None
    mgr.last_user_message_time = None
    mgr._activity_tracker = _FakeActivityTracker()

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


@pytest.mark.unit
def test_clean_frontend_memory_text_strips_c0_and_c1_controls():
    mgr = _make_manager()

    assert core_module.LLMSessionManager._clean_frontend_memory_text(
        mgr,
        " hello\x00 \x85world\x9f ",
    ) == "hello world"


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
async def test_voice_plugin_observer_noop_preserves_user_context_side_effects():
    mgr = _make_transcript_manager()
    routed = []

    async def fake_voice_broadcast(text):
        routed.append(text)
        return None

    mgr._broadcast_voice_transcript_observed = fake_voice_broadcast

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "  f(x)=x^3 derivative answer is 3x^2  ",
        is_voice_source=True,
    )
    await asyncio.sleep(0)

    assert routed == ["f(x)=x^3 derivative answer is 3x^2"]
    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["  f(x)=x^3 derivative answer is 3x^2  "]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "  f(x)=x^3 derivative answer is 3x^2  ",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "f(x)=x^3 derivative answer is 3x^2"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_bridge_session_change_continues_ordinary_transcript_flow():
    mgr = _make_transcript_manager()
    original_session = mgr.session
    replacement_session = object()
    routed = []

    async def fake_voice_broadcast(text):
        routed.append(text)
        mgr.session = replacement_session
        return None

    mgr._broadcast_voice_transcript_observed = fake_voice_broadcast

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "  Yui explain this step  ",
        is_voice_source=True,
    )
    await asyncio.sleep(0)

    assert routed == ["Yui explain this step"]
    assert original_session is not replacement_session
    assert mgr.session is replacement_session
    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["  Yui explain this step  "]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "  Yui explain this step  ",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "Yui explain this step"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_observer_broadcast_failure_continues_ordinary_transcript_flow(monkeypatch):
    mgr = _make_transcript_manager()
    mgr.session = _FakeVoiceBridgeSession()
    called = asyncio.Event()

    async def fake_publish(*_args, **_kwargs):
        called.set()
        raise RuntimeError("broadcast failed")

    monkeypatch.setattr(
        core_module,
        "publish_voice_transcript_observed_best_effort",
        fake_publish,
    )

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "  continue this transcript  ",
        is_voice_source=True,
    )
    await asyncio.wait_for(called.wait(), timeout=1)
    assert mgr._activity_tracker.voice_rms_count == 1
    assert mgr._activity_tracker.user_messages == ["  continue this transcript  "]
    assert mgr._session_turn_count == 1
    mgr._publish_user_utterance_to_plugin_bus.assert_called_once_with(
        "  continue this transcript  ",
        is_voice_source=True,
    )
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "continue this transcript"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_observer_does_not_prime_gemini_context_from_main(monkeypatch):
    mgr = _make_transcript_manager()
    session = _FakeGeminiVoiceBridgeSession()
    mgr.session = session

    async def fake_publish(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        core_module,
        "publish_voice_transcript_observed_best_effort",
        fake_publish,
    )

    await core_module.LLMSessionManager._broadcast_voice_transcript_observed(
        mgr,
        "explain this screen",
    )

    assert session.primed == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_transcript_runs_mini_game_invite_keyword(monkeypatch):
    """语音口头回应 mini-game 邀请必须和打字 / 点按钮一样过关键词匹配器——否则
    语音用户说"现在不想玩"永远触发不了 decline 冷却，会被下一个 proactive tick
    当成隐式 dismiss（只抑制 5min），邀请反复重来。回归：handle_input_transcript
    必须把原话喂给 dispatch_text_user_message（与文本路径对偶）。"""
    mgr = _make_transcript_manager()
    seen = []
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message",
        lambda name, text: seen.append((name, text)),
    )

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "  现在不想玩  ", is_voice_source=True,
    )

    # 传原话（未 strip），matcher 内部自己 lower+strip；与文本路径一致
    assert seen == [("Lan", "  现在不想玩  ")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_voice_transcript_keyword_outcome_pushes_invite_resolved(monkeypatch):
    """关键词命中时，语音路径推 mini_game_invite_resolved 让前端 dismiss
    ChoicePrompt（accept 兼带 game_url 当 launch 信号）。"""
    mgr = _make_transcript_manager()
    mgr.websocket = MagicMock()
    mgr.websocket.send_json = AsyncMock()
    fake_state = MagicMock()
    fake_state.CONNECTED = fake_state
    mgr.websocket.client_state = fake_state
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message",
        lambda name, text: {
            "action": "open_game",
            "session_id": "sid-1",
            "game_url": "/soccer_demo?x=1",
            "game_type": "soccer",
        },
    )

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "好啊一起玩", is_voice_source=True,
    )

    mgr.websocket.send_json.assert_awaited_once()
    payload = mgr.websocket.send_json.await_args.args[0]
    assert payload == {
        "type": "mini_game_invite_resolved",
        "session_id": "sid-1",
        "action": "open_game",
        "game_url": "/soccer_demo?x=1",
        "game_type": "soccer",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_voice_transcript_skips_mini_game_invite_keyword(monkeypatch):
    """Non-voice transcript reuse skips invite keywords already handled by text input."""
    mgr = _make_transcript_manager()
    seen = []
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message",
        lambda name, text: seen.append((name, text)),
    )

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "现在不想玩", is_voice_source=False,
    )

    assert seen == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_input_transcript_callback_uses_non_voice_path(monkeypatch):
    """Text-mode session callbacks must not emit voice-only side effects."""
    mgr = _make_transcript_manager()
    seen = []
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message",
        lambda name, text: seen.append((name, text)),
    )

    await core_module.LLMSessionManager.handle_text_input_transcript(
        mgr, "现在不想玩",
    )

    assert seen == []
    assert mgr._activity_tracker.voice_rms_count == 0
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {"input_type": "transcript", "data": "现在不想玩"},
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_mode_image_input_is_mirrored_to_analyzer_queue(monkeypatch):
    """Text-mode screenshots must stay available to turn-end analysis."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    monkeypatch.setattr(core_module, "process_screen_data", AsyncMock(return_value="img-b64"))

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "screen", "data": "raw-image"},
    )

    mgr.session.stream_image.assert_awaited_once_with("img-b64")
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {
            "input_type": "screen",
            "data": "data:image/jpeg;base64,img-b64",
            "has_image": True,
            "mime_type": "image/jpeg",
        },
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_mode_avatar_drop_image_is_metadata_only_in_analyzer_queue(monkeypatch):
    """Avatar Drop images must not put full base64 payloads into the sync queue."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    monkeypatch.setattr(core_module, "process_screen_data", AsyncMock(return_value="img-b64"))

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {
            "input_type": "avatar_drop_image",
            "data": "raw-image",
            "request_id": "req-img",
            "source": "avatar-drop",
        },
    )

    mgr.session.stream_image.assert_awaited_once_with("img-b64")
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {
            "input_type": "avatar_drop_image",
            "data": "",
            "has_image": True,
            "mime_type": "image/jpeg",
            "request_id": "req-img",
            "source": "avatar-drop",
        },
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_voice_transcript_reuse_preserves_avatar_drop_source():
    """Text-mode Avatar Drop memory summaries must keep their source tag."""
    mgr = _make_transcript_manager()

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "Handed over: note.txt",
        is_voice_source=False,
        source="avatar-drop",
        metadata={"source": "avatar-drop"},
    )

    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {
            "input_type": "transcript",
            "data": "Handed over: note.txt",
            "source": "avatar-drop",
            "metadata": {"source": "avatar-drop"},
        },
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_openclaw_magic_command_skips_local_text_stream(monkeypatch):
    """Namespaced OpenClaw slash commands use the manual-control fast path only."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}
    fired = []

    def fake_fire_task(coro):
        fired.append(coro)
        coro.close()

    mgr._fire_task = fake_fire_task
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw stop", "request_id": "req-1"},
    )

    assert len(fired) == 1
    mgr.session.stream_text.assert_not_called()
    assert mgr.sync_message_queue.messages == [
        {
            "type": "user",
            "data": {
                "input_type": "mirror_text",
                "data": "/openclaw stop",
                "source": "openclaw",
                "metadata": {
                    "source": "openclaw",
                    "kind": "magic_command",
                    "command": "/stop",
                },
                "request_id": "req-1",
            },
        },
        {
            "type": "system",
            "data": "turn end agent_callback",
            "request_id": "req-1",
        },
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openclaw_magic_command_falls_back_when_openclaw_not_ready(monkeypatch):
    """A stale OpenClaw flag must not swallow local text replies."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": False}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw stop", "request_id": "req-stale"},
    )

    mgr._fire_task.assert_not_called()
    mgr.session.stream_text.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_openclaw_magic_command_reuses_adapter_aliases(monkeypatch):
    """The immediate fast path must map namespaced aliases to OpenClaw commands."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}
    fired = []

    def fake_fire_task(coro):
        fired.append(coro)
        coro.close()

    mgr._fire_task = fake_fire_task
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw APPROVE", "request_id": "req-approve"},
    )

    assert len(fired) == 1
    mgr.session.stream_text.assert_not_called()
    assert mgr.sync_message_queue.messages[0]["data"]["metadata"]["command"] == "/daemon approve"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bare_openclaw_magic_words_do_not_short_circuit_text_stream(monkeypatch):
    """Generic slash commands are left for normal text/action handling."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/stop", "request_id": "req-stop"},
    )

    mgr._fire_task.assert_not_called()
    mgr.session.stream_text.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_openclaw_magic_command_clears_pending_text_images(monkeypatch):
    """Magic-command handoff must not leak queued screenshots into the next text turn."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = ["old-screen"]
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}

    def fake_fire_task(coro):
        coro.close()

    mgr._fire_task = fake_fire_task
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw new", "request_id": "req-new"},
    )

    assert mgr.session._pending_images == []
    assert mgr.session.stream_text.await_count == 0
    assert mgr.sync_message_queue.messages[-1]["data"] == "turn end agent_callback"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_late_magic_command_screenshot_is_discarded(monkeypatch):
    """Late screenshots for a magic-command request must not leak into later text turns."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}

    def fake_fire_task(coro):
        coro.close()

    mgr._fire_task = fake_fire_task
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)
    monkeypatch.setattr(core_module, "process_screen_data", AsyncMock(return_value="late-img"))

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw stop", "request_id": "req-stop"},
    )
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "screen", "data": "raw-image", "request_id": "req-stop"},
    )

    mgr.session.stream_image.assert_not_awaited()
    assert mgr.session._pending_images == []
    assert all(
        msg.get("data", {}).get("input_type") != "screen"
        for msg in mgr.sync_message_queue.messages
        if isinstance(msg.get("data"), dict)
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_openclaw_magic_command_emits_websocket_turn_end(monkeypatch):
    """Magic-command fast path must clear the matching frontend request."""
    mgr = _make_transcript_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": True}

    def fake_fire_task(coro):
        coro.close()

    mgr._fire_task = fake_fire_task
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "/openclaw stop", "request_id": "req-stop"},
    )

    assert mgr.websocket.sent == [{
        "type": "system",
        "data": "turn end agent_callback",
        "request_id": "req-stop",
    }]
    assert mgr.sync_message_queue.messages[-1] == mgr.websocket.sent[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openclaw_magic_command_publish_failure_reports_status(monkeypatch):
    """Manual OpenClaw command dispatch failures must be visible to users."""
    mgr = _make_transcript_manager()
    sent_statuses = []

    async def fake_send_status(message):
        sent_statuses.append(core_module.json.loads(message))

    mgr.send_status = fake_send_status
    monkeypatch.setattr(
        core_module,
        "publish_analyze_request_reliably",
        AsyncMock(return_value=False),
    )

    await core_module.LLMSessionManager._publish_openclaw_magic_command(
        mgr,
        "/stop",
    )

    assert sent_statuses == [{
        "code": "OPENCLAW_COMMAND_DISPATCH_FAILED",
        "details": {"command": "/stop"},
    }]


@pytest.mark.unit
def test_late_text_mode_screenshot_does_not_attach_to_next_turn():
    """Request-tagged screenshots must not leak into a later analyzer turn."""
    pending = [
        {"data": "data:image/jpeg;base64,old", "request_id": "req-old"},
        {"data": "data:image/jpeg;base64,current", "request_id": "req-current"},
        "data:image/jpeg;base64,legacy",
    ]

    selected = cross_server_module._select_pending_user_images_for_turn(pending, "req-current")
    recent = cross_server_module._build_recent_analyze_messages(
        [{"role": "user", "content": [{"type": "text", "text": "what now"}]}],
        selected,
        allow_attach_to_last_user=True,
    )

    assert selected == [
        {"data": "data:image/jpeg;base64,current", "request_id": "req-current"},
    ]
    attachments = recent[-1]["attachments"]
    urls = [item["url"] for item in attachments]
    assert urls == ["data:image/jpeg;base64,current"]
    assert "data:image/jpeg;base64,old" not in urls
    assert "data:image/jpeg;base64,legacy" not in urls


@pytest.mark.unit
def test_live_screen_frame_without_request_id_attaches_to_tagged_turn():
    """Live screen-share frames without request ids still belong to the active turn."""
    pending = [
        {"data": "data:image/jpeg;base64,old", "request_id": "req-old"},
        {"data": "data:image/jpeg;base64,live", "request_id": ""},
        "data:image/jpeg;base64,legacy",
    ]

    selected = cross_server_module._select_pending_user_images_for_turn(pending, "req-current")
    recent = cross_server_module._build_recent_analyze_messages(
        [{"role": "user", "content": [{"type": "text", "text": "what is on screen"}]}],
        selected,
        allow_attach_to_last_user=True,
    )

    assert selected == [
        {"data": "data:image/jpeg;base64,live", "request_id": ""},
    ]
    urls = [item["url"] for item in recent[-1]["attachments"]]
    assert urls == ["data:image/jpeg;base64,live"]


@pytest.mark.unit
def test_turn_image_partition_retains_later_request_images():
    """An earlier turn end must not clear screenshots already tagged for a later turn."""
    pending = [
        {"data": "data:image/jpeg;base64,first", "request_id": "req-first"},
        {"data": "data:image/jpeg;base64,next", "request_id": "req-next"},
        {"data": "data:image/jpeg;base64,live", "request_id": ""},
        "data:image/jpeg;base64,legacy",
    ]

    selected, remaining = cross_server_module._partition_pending_user_images_for_turn(pending, "req-first")

    assert selected == [
        {"data": "data:image/jpeg;base64,first", "request_id": "req-first"},
        {"data": "data:image/jpeg;base64,live", "request_id": ""},
    ]
    assert remaining == [
        {"data": "data:image/jpeg;base64,next", "request_id": "req-next"},
    ]


@pytest.mark.unit
def test_turn_image_partition_retains_untagged_images_without_user_input():
    """Agent/proactive turn ends must not steal image-only screenshots before the user's text."""
    pending = [
        {"data": "data:image/jpeg;base64,screen", "request_id": ""},
        "data:image/jpeg;base64,legacy",
    ]

    selected, remaining = cross_server_module._partition_pending_user_images_for_turn(
        pending,
        None,
        consume_untagged=False,
    )

    assert selected == []
    assert remaining == pending


@pytest.mark.unit
def test_cross_server_avatar_drop_image_queue_skips_metadata_only_entries():
    """Cross-server sync may carry real image data, but not metadata-only Avatar Drop placeholders."""
    pending = []

    appended = cross_server_module._append_pending_user_image(
        pending,
        "data:image/jpeg;base64,current",
        "req-current",
        "user_image",
    )
    skipped = cross_server_module._append_pending_user_image(
        pending,
        "",
        "req-current",
        "avatar_drop_image",
    )

    assert appended is True
    assert skipped is False
    assert pending == [{
        "data": "data:image/jpeg;base64,current",
        "request_id": "req-current",
        "input_type": "user_image",
    }]


@pytest.mark.unit
def test_avatar_drop_recent_message_marks_latest_user_for_analyzer_skip():
    """Avatar Drop handoff turns are chat content, not Agent task requests."""
    metadata = {"sources": [cross_server_module.AVATAR_DROP_SOURCE]}
    recent = cross_server_module._build_recent_analyze_messages(
        [{
            "role": "user",
            "content": [{"type": "text", "text": "Handed over: note.txt"}],
            "source": cross_server_module.AVATAR_DROP_SOURCE,
            "metadata": metadata,
        }],
        [{
            "data": "data:image/png;base64,current",
            "request_id": "req-current",
            "input_type": "avatar_drop_image",
            "source": cross_server_module.AVATAR_DROP_SOURCE,
        }],
        allow_attach_to_last_user=True,
    )

    assert recent == [{
        "role": "user",
        "content": "Handed over: note.txt",
        "source": cross_server_module.AVATAR_DROP_SOURCE,
        "metadata": {"sources": [cross_server_module.AVATAR_DROP_SOURCE]},
        "attachments": [{
            "type": "image_url",
            "url": "data:image/png;base64,current",
            "input_type": "avatar_drop_image",
            "source": cross_server_module.AVATAR_DROP_SOURCE,
        }],
    }]
    assert recent[0]["metadata"] is not metadata
    assert cross_server_module._latest_user_message_has_source(
        recent,
        cross_server_module.AVATAR_DROP_SOURCE,
    ) is True


@pytest.mark.unit
def test_avatar_drop_source_on_older_user_message_does_not_skip_latest_normal_user():
    """Only the latest user turn controls the analyzer skip decision."""
    recent = [
        {
            "role": "user",
            "content": "Handed over: note.txt",
            "source": cross_server_module.AVATAR_DROP_SOURCE,
        },
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "Now help me open settings."},
    ]

    assert cross_server_module._latest_user_message_has_source(
        recent,
        cross_server_module.AVATAR_DROP_SOURCE,
    ) is False


@pytest.mark.unit
def test_session_end_request_tagged_screenshot_selection_falls_back_to_latest_request():
    """Session-end cleanup may not carry request_id, but must not drop tagged images."""
    pending = [
        {"data": "data:image/jpeg;base64,old", "request_id": "req-old"},
        {"data": "data:image/jpeg;base64,current", "request_id": "req-current"},
        "data:image/jpeg;base64,legacy",
    ]

    selected = cross_server_module._select_pending_user_images_for_session_end(pending, None)
    recent = cross_server_module._build_recent_analyze_messages(
        [{"role": "user", "content": [{"type": "text", "text": "bye"}]}],
        selected,
        allow_attach_to_last_user=True,
    )

    assert selected == [
        {"data": "data:image/jpeg;base64,current", "request_id": "req-current"},
    ]
    urls = [item["url"] for item in recent[-1]["attachments"]]
    assert urls == ["data:image/jpeg;base64,current"]
    assert "data:image/jpeg;base64,old" not in urls
    assert "data:image/jpeg;base64,legacy" not in urls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_genuine_voice_transcript_stamps_last_user_message_time(monkeypatch):
    """真实非空语音消息既刷 last_user_activity_time 也刷 last_user_message_time。
    后者喂给 mini-game 邀请隐式 dismiss，必须只反映真用户输入。"""
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "今天天气不错", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == FIXED_TS
    assert mgr.last_user_message_time == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_echo_transcript_does_not_stamp_last_user_message_time(monkeypatch):
    """关键回归：AI 念邀请台词被麦克风录回的回声会刷 last_user_activity_time
    （顶部无条件），但**不能**刷 last_user_message_time——否则语音模式下用户还
    没点「现在不想玩」按钮，隐式 dismiss 就因回声误判用户已回应、把 pending 邀请
    清掉撤按钮，用户随后点击落到 expired、邀请 5min 后反复重来。"""
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "要不要现在跟我一起踢一会儿足球小游戏？"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "要不要现在跟我一起踢一会儿足球小游戏", is_voice_source=True,
    )

    # 回声照样污染 last_user_activity_time（说明旧字段为何不能用于邀请判定）
    assert mgr.last_user_activity_time == FIXED_TS
    # 但真消息时间戳保持干净
    assert mgr.last_user_message_time is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_voice_transcript_does_not_stamp_last_user_message_time(monkeypatch):
    """空转录（VAD 误触发 / 转录失败）刷 activity 但不刷真消息时间戳。"""
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "   ", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == FIXED_TS
    assert mgr.last_user_message_time is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_last_user_message_time_uses_transcript_arrival_not_post_await(monkeypatch):
    """takeover dispatcher 注册但未消费该转写时，last_user_message_time 必须用转写
    到达时刻（await 之前），不能用 await 之后的 time.time()——否则 await 期间投递的
    invite 会把 invite 之前的发言误记成之后的回应、提前清掉 pending invite（codex
    P2）。time.time() 每次递增，断言两个时间戳都锁在首次（到达）取值 101。"""
    mgr = _make_transcript_manager()
    calls = {"n": 0}

    def _ticking_time():
        calls["n"] += 1
        return 100.0 + calls["n"]

    monkeypatch.setattr(core_module.time, "time", _ticking_time)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    async def _dispatcher(name, text, request_id=None):
        core_module.time.time()  # 模拟 await 期间时钟流逝
        return False             # 未处理 → 继续普通流程走到真消息块

    mgr._takeover_input_dispatcher = _dispatcher
    mgr.session = object()

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "你好呀", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == 101.0
    assert mgr.last_user_message_time == 101.0


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
def test_neko_live_reply_repeat_detects_chinese_paraphrase():
    assert core_module._looks_like_repeated_neko_live_reply(
        "我可是随时准备着给你们惊喜的喵",
        "我可是随时准备给你们惊喜的喵",
    )


@pytest.mark.unit
def test_neko_live_reply_repeat_detects_same_host_beat_with_changed_words():
    assert core_module._looks_like_repeated_neko_live_reply(
        "小鱼干奖励先记账，等弹幕接一句",
        "给你们备了鱼干小奖励，谁先发弹幕",
    )


@pytest.mark.unit
def test_neko_live_reply_repeat_detects_same_reward_bit_with_low_word_overlap():
    assert core_module._looks_like_repeated_neko_live_reply(
        "小鱼干先记账",
        "奖励小本本又打开了",
    )


@pytest.mark.unit
def test_neko_live_reply_repeat_detects_same_host_score_bit_with_changed_words():
    assert core_module._looks_like_repeated_neko_live_reply(
        "猫猫主播力先满格三秒",
        "正经主持挑战开始，别笑",
    )


@pytest.mark.unit
def test_neko_live_reply_repeat_keeps_different_short_lines_apart():
    assert not core_module._looks_like_repeated_neko_live_reply(
        "小鱼干先记账",
        "今天先看弹幕",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_marks_repeated_neko_live_reply_metadata(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    mgr.current_speech_id = "live-turn-1"
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "猫猫先蹲一下",
        metadata=metadata,
    )
    mgr.current_speech_id = "live-turn-2"
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "猫猫先蹲一下！",
        metadata=metadata,
    )

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    assert first["neko_live_reply_repeat"] is False
    assert second["neko_live_reply_repeat"] is True
    assert second["neko_live_reply_repeat_window"] == 2
    assert mgr._neko_live_recent_reply_text == "猫猫先蹲一下！"
    assert metadata.get("neko_live_reply_repeat") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_marks_non_adjacent_neko_live_reply_repeat():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    mgr.current_speech_id = "live-turn-1"
    await core_module.LLMSessionManager.send_lanlan_response(mgr, "cat says tiny plan", metadata=metadata)
    mgr.current_speech_id = "live-turn-2"
    await core_module.LLMSessionManager.send_lanlan_response(mgr, "fresh different angle", metadata=metadata)
    mgr.current_speech_id = "live-turn-3"
    await core_module.LLMSessionManager.send_lanlan_response(mgr, "cat says tiny plan!", metadata=metadata)

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    third = mgr.sync_message_queue.messages[2]["data"]["metadata"]
    assert first["neko_live_reply_repeat"] is False
    assert second["neko_live_reply_repeat"] is False
    assert third["neko_live_reply_repeat"] is True
    assert third["neko_live_reply_repeat_window"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_detects_older_neko_live_reply_repeat_inside_wider_window():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }
    outputs = [
        "cat says tiny plan",
        "fresh desk charm",
        "small moon check",
        "quiet room blink",
        "one word vote",
        "tiny snack compass",
        "soft corner tease",
        "room light report",
        "cat says tiny plan!",
    ]

    for index, output in enumerate(outputs, start=1):
        mgr.current_speech_id = f"live-turn-{index}"
        await core_module.LLMSessionManager.send_lanlan_response(mgr, output, metadata=metadata)

    last = mgr.sync_message_queue.messages[-1]["data"]["metadata"]
    assert last["neko_live_reply_repeat"] is True
    assert last["neko_live_reply_repeat_window"] == 9


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_prunes_neko_live_repeat_history_before_detection():
    mgr = _make_manager()
    mgr._neko_live_recent_reply_texts = OrderedDict(
        [("live-turn-0", "ancient moon phrase")]
        + [(f"live-turn-{index}", f"fresh angle {index}") for index in range(1, 26)]
    )
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    mgr.current_speech_id = "live-turn-new"
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "ancient moon phrase!",
        metadata=metadata,
    )

    saved = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    assert saved["neko_live_reply_repeat"] is False
    assert saved["neko_live_reply_repeat_window"] == 24
    assert list(mgr._neko_live_recent_reply_texts.values()) == [
        *(f"fresh angle {index}" for index in range(3, 26)),
        "ancient moon phrase!",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_shapes_neko_live_reply_to_first_sentence():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
        "max_reply_chars": 24,
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "第一句刚好很短！第二句不该播出来。",
        metadata=metadata,
    )

    message = mgr.sync_message_queue.messages[0]["data"]
    saved = message["metadata"]
    assert message["text"] == "第一句刚好很短！"
    assert saved["neko_live_reply_shaped"] is True
    assert saved["neko_live_reply_shape_reason"] == "first_sentence"
    assert saved["neko_live_reply_output_chars"] == len("第一句刚好很短！")
    assert saved["neko_live_reply_original_chars"] == len("第一句刚好很短！第二句不该播出来。")
    assert metadata.get("neko_live_reply_shaped") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_clips_long_neko_live_reply_without_sentence_break():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "warmup_hosting",
        "max_reply_chars": 12,
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "这一整段没有停顿会变成很长很长的直播回复",
        metadata=metadata,
    )

    message = mgr.sync_message_queue.messages[0]["data"]
    saved = message["metadata"]
    assert message["text"] == "这一整段没有停顿会变成很"
    assert len(message["text"]) == 12
    assert saved["neko_live_reply_shaped"] is True
    assert saved["neko_live_reply_shape_reason"] == "max_reply_chars"
    assert saved["neko_live_reply_output_chars"] == 12


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_queues_shaped_neko_live_reply_for_tts():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
        "max_reply_chars": 28,
    }
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "第一句刚好很短！第二句不该进入语音。",
        metadata=metadata,
        mirror_text=True,
        emit_turn_end_after=False,
    )

    message = mgr.sync_message_queue.messages[0]["data"]
    assert result["ok"] is True
    assert result["audio_queued"] is True
    assert message["text"] == "第一句刚好很短！"
    assert mgr.tts_request_queue.messages[0][1] == "第一句刚好很短！"
    assert result["metadata"]["neko_live_reply_shaped"] is True
    assert result["metadata"]["neko_live_reply_shape_reason"] == "first_sentence"
    assert message["metadata"]["neko_live_reply_shaped"] is True
    assert metadata.get("neko_live_reply_shaped") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_marks_similar_neko_live_reply_repeat():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    mgr.current_speech_id = "live-turn-1"
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat is ready with a tiny surprise",
        metadata=metadata,
    )
    mgr.current_speech_id = "live-turn-2"
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat is ready with tiny surprises",
        metadata=metadata,
    )

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    assert first["neko_live_reply_repeat"] is False
    assert second["neko_live_reply_repeat"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_suppresses_repeated_neko_live_first_chunk():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    sent = await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan!",
        is_first_chunk=True,
        turn_id="live-turn-2",
        metadata=metadata,
    )

    assert sent is False
    assert len(mgr.sync_message_queue.messages) == 1
    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    assert first["neko_live_reply_repeat"] is False
    assert mgr._last_neko_live_reply_suppressed is True
    assert list(mgr._neko_live_recent_reply_texts.values()) == ["cat says tiny plan"]
    assert mgr._current_ai_turn_text == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_suppresses_repeated_neko_live_reply_stream_turn():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    first_chunk_sent = await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat",
        is_first_chunk=True,
        turn_id="live-turn-2",
        metadata=metadata,
    )
    second_chunk_sent = await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        " says tiny plan!",
        is_first_chunk=False,
        turn_id="live-turn-2",
        metadata=metadata,
    )

    assert first_chunk_sent is False
    assert second_chunk_sent is False
    assert len(mgr.sync_message_queue.messages) == 2
    assert mgr.sync_message_queue.messages[-1]["data"]["text"] == "cat"
    assert mgr._last_neko_live_reply_suppressed is True
    tracked_meta = mgr._last_tracked_neko_live_reply_metadata(metadata)
    assert tracked_meta["neko_live_reply_repeat"] is True
    assert tracked_meta["neko_live_reply_suppressed"] == "repeat"
    assert list(mgr._neko_live_recent_reply_texts.values()) == [
        "cat says tiny plan",
        "cat",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_output_reports_suppressed_repeated_neko_live_reply():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )

    result = await core_module.LLMSessionManager.mirror_assistant_output(
        mgr,
        "cat says tiny plan!",
        metadata=metadata,
        turn_id="live-turn-2",
    )

    assert result["ok"] is False
    assert result["reason"] == "repeated_live_reply"
    assert result["mirrored"] is False
    assert result["metadata"]["neko_live_reply_repeat"] is True
    assert result["metadata"]["neko_live_reply_suppressed"] == "repeat"
    assert len(mgr.sync_message_queue.messages) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_does_not_queue_audio_for_suppressed_neko_live_repeat():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "cat says tiny plan!",
        metadata=metadata,
        mirror_text=True,
        emit_turn_end_after=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "repeated_live_reply"
    assert result["audio_sent"] is False
    assert result["audio_queued"] is False
    assert result["metadata"]["neko_live_reply_repeat"] is True
    assert result["metadata"]["neko_live_reply_suppressed"] == "repeat"
    assert result["turn_end_emitted"] is True
    assert mgr.sync_message_queue.messages[-1]["meta"]["neko_live_reply_suppressed"] == "repeat"
    assert len(mgr.tts_request_queue.messages) == 0
    assert len(mgr.tts_pending_chunks) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_suppresses_voice_only_neko_live_repeat():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "cat says tiny plan!",
        metadata=metadata,
        mirror_text=False,
        emit_turn_end_after=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "repeated_live_reply"
    assert result["audio_sent"] is False
    assert result["audio_queued"] is False
    assert result["metadata"]["neko_live_reply_repeat"] is True
    assert result["metadata"]["neko_live_reply_suppressed"] == "repeat"
    assert result["turn_end_emitted"] is True
    assert mgr.sync_message_queue.messages[-1]["meta"]["neko_live_reply_suppressed"] == "repeat"
    assert len(mgr.tts_request_queue.messages) == 0
    assert len(mgr.tts_pending_chunks) == 0
    assert list(mgr._neko_live_recent_reply_texts.values()) == ["cat says tiny plan"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_tracks_voice_only_neko_live_without_text_mirror():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "fresh tiny host beat",
        metadata=metadata,
        mirror_text=False,
        emit_turn_end_after=False,
    )

    assert result["ok"] is True
    assert result["audio_queued"] is True
    assert result["metadata"]["neko_live_reply_repeat"] is False
    assert result["turn_end_emitted"] is False
    assert len(mgr.tts_request_queue.messages) > 0
    assert list(mgr._neko_live_recent_reply_texts.values()) == ["fresh tiny host beat"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_aggregates_neko_live_streaming_chunks_by_turn():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says ",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "tiny plan",
        is_first_chunk=False,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "fresh different angle",
        is_first_chunk=True,
        turn_id="live-turn-2",
        metadata=metadata,
    )

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    third = mgr.sync_message_queue.messages[2]["data"]["metadata"]
    assert first["neko_live_reply_repeat_window"] == 1
    assert second["neko_live_reply_repeat_window"] == 1
    assert second["neko_live_reply_repeat"] is False
    assert third["neko_live_reply_repeat"] is False
    assert third["neko_live_reply_repeat_window"] == 2
    assert list(mgr._neko_live_recent_reply_texts.values()) == [
        "cat says tiny plan",
        "fresh different angle",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_merges_neko_live_full_buffer_snapshots_by_turn():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says ",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=False,
        turn_id="live-turn-1",
        metadata=metadata,
    )

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    assert first["neko_live_reply_repeat_window"] == 1
    assert second["neko_live_reply_repeat_window"] == 1
    assert second["neko_live_reply_repeat"] is False
    assert list(mgr._neko_live_recent_reply_texts.values()) == [
        "cat says tiny plan",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_detects_repeated_neko_live_reply_without_turn_id():
    mgr = _make_manager()
    mgr.current_speech_id = None
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    await core_module.LLMSessionManager.send_lanlan_response(mgr, "cat says tiny plan", metadata=metadata)
    await core_module.LLMSessionManager.send_lanlan_response(mgr, "cat says tiny plan!", metadata=metadata)

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    assert first["neko_live_reply_repeat"] is False
    assert second["neko_live_reply_repeat"] is True
    assert second["neko_live_reply_repeat_window"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_migrates_legacy_recent_live_reply_values():
    mgr = _make_manager()
    mgr._neko_live_recent_reply_texts = deque(["cat says tiny plan"], maxlen=6)
    mgr.current_speech_id = "live-turn-2"
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr, "cat says tiny plan!", metadata=metadata
    )

    saved = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    assert saved["neko_live_reply_repeat"] is True
    assert saved["neko_live_reply_repeat_window"] == 2
    assert list(mgr._neko_live_recent_reply_texts.values()) == [
        "cat says tiny plan",
        "cat says tiny plan!",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_callback_buffers_proactive_live_reply_metadata_until_flush():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says tiny plan", is_first_chunk=True
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert mgr.sent_responses == []
    await core_module.LLMSessionManager._flush_neko_live_reply_output_buffer(
        mgr, log_context="unit-test"
    )

    saved_metadata = mgr.sent_responses[0]["metadata"]
    assert saved_metadata["plugin"] == metadata["plugin"]
    assert saved_metadata["live_reply_contract"] == metadata["live_reply_contract"]
    assert saved_metadata["response_module_hint"] == metadata["response_module_hint"]
    assert saved_metadata["neko_live_reply_shaped"] is False
    assert mgr.sent_responses[0]["text"] == "cat says tiny plan"
    assert mgr.sent_responses[0]["is_first_chunk"] is True
    assert metadata.get("neko_live_reply_repeat") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_transcript_buffers_sid_cached_proactive_live_reply_metadata_until_flush():
    mgr = _make_manager()
    mgr.current_speech_id = "live-sid"
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
    }

    core_module.LLMSessionManager._remember_proactive_live_reply_metadata(
        mgr, "live-sid", metadata
    )
    await core_module.LLMSessionManager.handle_output_transcript(
        mgr, "cat says tiny plan", is_first_chunk=True
    )

    assert mgr.sent_responses == []
    await core_module.LLMSessionManager._flush_neko_live_reply_output_buffer(
        mgr, log_context="unit-test"
    )

    saved_metadata = mgr.sent_responses[0]["metadata"]
    assert saved_metadata["plugin"] == metadata["plugin"]
    assert saved_metadata["live_reply_contract"] == metadata["live_reply_contract"]
    assert saved_metadata["response_module_hint"] == metadata["response_module_hint"]
    assert saved_metadata["neko_live_reply_shaped"] is False
    assert mgr.sent_responses[0]["text"] == "cat says tiny plan"
    assert mgr.sent_responses[0]["turn_id"] == "live-sid"
    assert metadata.get("neko_live_reply_repeat") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_text_callback_neko_live_buffer_suppresses_repeat_before_prefix_reaches_queue():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "cat says tiny plan",
        is_first_chunk=True,
        turn_id="live-turn-1",
        metadata=metadata,
    )
    mgr.current_speech_id = "live-turn-2"
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, " says tiny plan!", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert len(mgr.sync_message_queue.messages) == 1
    flushed = await core_module.LLMSessionManager._flush_neko_live_reply_output_buffer(
        mgr, log_context="unit-test"
    )

    assert flushed is False
    assert len(mgr.sync_message_queue.messages) == 1
    assert mgr._last_neko_live_reply_suppressed is True
    tracked_meta = mgr._last_tracked_neko_live_reply_metadata(metadata)
    assert tracked_meta["neko_live_reply_repeat"] is True
    assert tracked_meta["neko_live_reply_suppressed"] == "repeat"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_complete_flushes_buffered_neko_live_reply_once():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-buffered"
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says ", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "tiny plan", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert mgr.sync_message_queue.messages == []

    await core_module.LLMSessionManager.handle_response_complete(mgr)

    assert len(mgr.sync_message_queue.messages) == 1
    message = mgr.sync_message_queue.messages[0]["data"]
    assert message["text"] == "cat says tiny plan"
    assert message["isNewMessage"] is True
    assert message["turn_id"] == "live-turn-buffered"
    assert message["metadata"]["response_module_hint"] == "danmaku_response"
    assert message["metadata"]["neko_live_reply_repeat"] is False
    assert list(mgr._neko_live_recent_reply_texts.values()) == ["cat says tiny plan"]
    mgr._emit_turn_end.assert_awaited_once_with(None)
    mgr._finalize_turn_after_emit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_complete_flushes_buffered_neko_live_reply_to_tts_after_text():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-tts-buffered"
    mgr.use_tts = True
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says ", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "tiny plan", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert mgr.sync_message_queue.messages == []
    assert mgr.tts_request_queue.messages == []

    await core_module.LLMSessionManager.handle_response_complete(mgr)

    assert mgr.sync_message_queue.messages[0]["data"]["text"] == "cat says tiny plan"
    assert mgr.tts_request_queue.messages == [
        ("live-turn-tts-buffered", "cat says tiny plan"),
        (None, None),
    ]
    assert mgr._tts_done_queued_for_turn is True
    mgr._emit_turn_end.assert_awaited_once_with(None)
    mgr._finalize_turn_after_emit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_response_discarded_clears_buffered_neko_live_reply_without_output():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-discarded"
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says ", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "tiny plan", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert isinstance(mgr._neko_live_reply_output_buffer, dict)
    assert mgr.sync_message_queue.messages == []

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        reason="unit-test",
        attempt=1,
        max_attempts=2,
        will_retry=True,
    )

    assert mgr._neko_live_reply_output_buffer is None
    assert mgr.sync_message_queue.messages == [
        {"type": "system", "data": "response_discarded_clear"}
    ]
    assert not hasattr(mgr, "_neko_live_recent_reply_text")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proactive_complete_flushes_buffered_neko_live_reply_once():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-proactive"
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says ", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "tiny plan", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert mgr.sync_message_queue.messages == []

    await core_module.LLMSessionManager.handle_proactive_complete(mgr)

    assert len(mgr.sync_message_queue.messages) == 2
    message = mgr.sync_message_queue.messages[0]["data"]
    assert message["text"] == "cat says tiny plan"
    assert message["turn_id"] == "live-turn-proactive"
    assert message["metadata"]["response_module_hint"] == "active_engagement"
    assert mgr.sync_message_queue.messages[1] == {
        "type": "system",
        "data": "turn end agent_callback",
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proactive_complete_flushes_buffered_neko_live_reply_to_tts_before_done():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-proactive-tts"
    mgr.use_tts = True
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says ", is_first_chunk=True
        )
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "tiny plan", is_first_chunk=False
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert mgr.sync_message_queue.messages == []
    assert mgr.tts_request_queue.messages == []

    await core_module.LLMSessionManager.handle_proactive_complete(mgr)

    assert mgr.sync_message_queue.messages[0]["data"]["text"] == "cat says tiny plan"
    assert mgr.tts_request_queue.messages == [
        ("live-turn-proactive-tts", "cat says tiny plan"),
        (None, None),
    ]
    assert mgr._tts_done_queued_for_turn is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_proactive_complete_without_content_clears_buffered_neko_live_reply():
    mgr = _make_manager()
    delattr(mgr, "send_lanlan_response")
    mgr.current_speech_id = "live-turn-proactive-empty"
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "active_engagement",
    }
    token = core_module._proactive_live_reply_metadata.set(metadata)
    try:
        await core_module.LLMSessionManager.handle_text_data(
            mgr, "cat says tiny plan", is_first_chunk=True
        )
    finally:
        core_module._proactive_live_reply_metadata.reset(token)

    assert isinstance(mgr._neko_live_reply_output_buffer, dict)

    await core_module.LLMSessionManager.handle_proactive_complete(
        mgr, content_committed=False
    )

    assert mgr._neko_live_reply_output_buffer is None
    assert mgr.sync_message_queue.messages == []
    assert not hasattr(mgr, "_neko_live_recent_reply_text")


@pytest.mark.unit
def test_sid_cached_proactive_live_reply_metadata_is_bounded():
    mgr = _make_manager()
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    for index in range(core_module._PROACTIVE_LIVE_REPLY_METADATA_BY_SID_MAX + 3):
        core_module.LLMSessionManager._remember_proactive_live_reply_metadata(
            mgr, f"sid-{index}", metadata
        )

    by_sid = mgr._proactive_live_reply_metadata_by_sid
    assert len(by_sid) == core_module._PROACTIVE_LIVE_REPLY_METADATA_BY_SID_MAX
    assert "sid-0" not in by_sid
    assert f"sid-{core_module._PROACTIVE_LIVE_REPLY_METADATA_BY_SID_MAX + 2}" in by_sid


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_does_not_mark_non_neko_live_reply_repeat():
    mgr = _make_manager()

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "普通回复",
        metadata={"plugin": "other"},
    )
    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "普通回复",
        metadata={"plugin": "other"},
    )

    first = mgr.sync_message_queue.messages[0]["data"]["metadata"]
    second = mgr.sync_message_queue.messages[1]["data"]["metadata"]
    assert "neko_live_reply_repeat" not in first
    assert "neko_live_reply_repeat" not in second
    assert not hasattr(mgr, "_neko_live_recent_reply_text")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_keeps_neko_live_reply_out_of_ordinary_ai_turn(monkeypatch):
    mgr = _make_manager()
    monkeypatch.setattr(core_module.time, "time", lambda: FIXED_TS)
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "danmaku_response",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "猫猫换个角度接这句",
        metadata=metadata,
        remember_voice_echo=True,
    )

    assert mgr._current_ai_turn_text == ""
    assert mgr._recent_ai_voice_echo_text == "猫猫换个角度接这句"
    assert mgr._recent_ai_voice_echo_at == FIXED_TS
    message = mgr.sync_message_queue.messages[0]["data"]
    assert message["text"] == "猫猫换个角度接这句"
    assert message["metadata"]["live_reply_contract"] == "short_tts_line"
    assert list(mgr._neko_live_recent_reply_texts.values()) == ["猫猫换个角度接这句"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_keeps_neko_live_reply_out_of_new_session_cache():
    mgr = _make_manager()
    mgr.is_preparing_new_session = True
    mgr.message_cache_for_new_session = []
    metadata = {
        "plugin": "neko_roast",
        "live_reply_contract": "short_tts_line",
        "response_module_hint": "idle_hosting",
    }

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "今晚别复读上一句",
        metadata=metadata,
    )

    assert mgr.message_cache_for_new_session == []
    message = mgr.sync_message_queue.messages[0]["data"]
    assert message["text"] == "今晚别复读上一句"
    assert message["metadata"]["live_reply_contract"] == "short_tts_line"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_keeps_ordinary_reply_in_new_session_cache():
    mgr = _make_manager()
    mgr.is_preparing_new_session = True
    mgr.message_cache_for_new_session = []

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "普通回复仍然预热新会话",
        metadata={"plugin": "normal_chat"},
    )

    assert mgr.message_cache_for_new_session == [
        {"role": mgr.lanlan_name, "text": "普通回复仍然预热新会话"}
    ]
    message = mgr.sync_message_queue.messages[0]["data"]
    assert message["text"] == "普通回复仍然预热新会话"
    assert message["metadata"]["plugin"] == "normal_chat"


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

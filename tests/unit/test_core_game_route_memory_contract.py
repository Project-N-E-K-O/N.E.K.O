import asyncio
import logging
from collections import deque
import queue
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, Mock

import pytest

import main_logic.cross_server as cross_server_module
import main_logic.core as core_module
import main_logic.core.streaming as streaming_module
import main_logic.core.tts_runtime as tts_runtime_module
import main_logic.core.turn as turn_module
from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
from tests.fake_clock import patch_module_clock

# 假时钟一律打到「真正读 time.time() 的那个模块」上，而不是 core_module
# （main_logic.core 是门面包，自身不读时钟）。本文件里三类被测方法分别落在：
#   - main_logic.core.turn      转写 / send_lanlan_response / 语音回声缓存
#   - main_logic.core.streaming 输入 ingress 时间戳（_stream_data_now 等）
#   - main_logic.core.tts_runtime  TTS 响应处理与管线清理
# 旧写法 `setattr(core_module.time, "time", ...)` 其实换掉了整个 stdlib time
# 模块，靠全局副作用才恰好覆盖到这些模块。


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

    def on_voice_rms(self):
        self.voice_rms_count += 1

    def on_user_message(self, text):
        self.user_messages.append(text)


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
    mgr.last_user_engagement_time = None

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
        # 真实实现在 track_ai_turn 为真时同步累加 AI turn buffer（turn end 时
        # 交给 activity tracker）。stub 不照做的话，凡是断言 buffer 内容的用例
        # 都会对"send 到底 track 了没有"失明。
        if _kwargs.get("track_ai_turn", True):
            mgr._current_ai_turn_text += text

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

    assert result["ok"] is False
    assert result["reason"] == "tts_unavailable"
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
async def test_mirror_assistant_speech_binds_relative_gain_to_its_speech_id():
    mgr = _make_manager()

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "大声一点",
        metadata=_soccer_mirror_meta({"kind": "mailbox"}),
        mirror_text=False,
        emit_turn_end_after=False,
        playback_gain=2.0,
    )

    assert result["playback_gain"] == 2.0
    assert mgr.speech_playback_gain(result["speech_id"]) == 2.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_replays_opted_in_cached_audio_without_requeueing_tts():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: ("opaque-cache-key", "voice-signature")
    sent_audio = []
    completed_audio = []

    # Cached replay goes out as ONE batch under a single frame-lock hold, so
    # the double records at that boundary rather than per frame.
    async def send_cached_speech_batch(chunks, speech_id):
        for audio in chunks:
            sent_audio.append((bytes(audio), speech_id))
        completed_audio.append(speech_id)
        return True, True

    mgr.send_cached_speech_batch = send_cached_speech_batch
    try:
        first = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "再来一球",
            metadata=_soccer_mirror_meta({"kind": "goal"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )
        assert first["cache_status"] == "miss"
        queued_before_hit = list(mgr.tts_request_queue.messages)
        assert GAME_SPEECH_AUDIO_CACHE.append_capture(mgr, first["speech_id"], b"cached-pcm")
        assert GAME_SPEECH_AUDIO_CACHE.complete_capture(
            mgr, first["speech_id"], "voice-signature"
        )

        second = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "再来一球",
            metadata=_soccer_mirror_meta({"kind": "goal"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )

        assert second["method"] == "project_tts_cache"
        assert second["cache_status"] == "hit"
        assert second["audio_sent"] is True
        # A cache hit only writes chunks to the socket, so completion is never
        # observed on this path and must not be claimed either way. Delivery is
        # reported by audio_sent; the sibling non-cache path likewise answers
        # None whenever completion was not awaited.
        assert second["audio_completed"] is None
        # A cache hit is written straight to the websocket: there is no worker
        # completion sentinel and no client acknowledgement, so "it was
        # delivered" is all this path can honestly report. Claiming completion
        # is supported here would tell a caller that asked to be notified when
        # the line finished playing that it had, when it was only queued.
        assert second["audio_completion_supported"] is False
        assert mgr.tts_request_queue.messages == queued_before_hit
        assert sent_audio == [(b"cached-pcm", second["speech_id"])]
        assert completed_audio == [second["speech_id"]]
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_speech_replay_reports_failed_audio_delivery():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: ("opaque-cache-key", "voice-signature")
    completed_audio = []

    async def failing_batch(chunks, speech_id):
        # Frames failed, terminal signal still went out -- the batch reports the
        # two independently for exactly this case.
        completed_audio.append(speech_id)
        return False, True
    try:
        first = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "缓存发送失败",
            metadata=_soccer_mirror_meta({"kind": "cache-failure"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )
        assert GAME_SPEECH_AUDIO_CACHE.append_capture(mgr, first["speech_id"], b"cached-pcm")
        assert GAME_SPEECH_AUDIO_CACHE.complete_capture(mgr, first["speech_id"], "voice-signature")

        mgr.send_cached_speech_batch = failing_batch
        replay = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "缓存发送失败",
            metadata=_soccer_mirror_meta({"kind": "cache-failure"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )

        assert replay["method"] == "project_tts_cache"
        assert replay["ok"] is False
        assert replay["audio_sent"] is False
        # A cache hit only writes chunks to the socket, so completion is never
        # observed on this path and must not be claimed either way. Delivery is
        # reported by audio_sent; the sibling non-cache path likewise answers
        # None whenever completion was not awaited.
        assert replay["audio_completed"] is None
        assert completed_audio == [replay["speech_id"]]
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_speech_replay_reports_failed_audio_done_delivery():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: ("opaque-cache-key", "voice-signature")
    try:
        first = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "缓存结束帧失败",
            metadata=_soccer_mirror_meta({"kind": "cache-done-failure"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )
        assert GAME_SPEECH_AUDIO_CACHE.append_capture(mgr, first["speech_id"], b"cached-pcm")
        assert GAME_SPEECH_AUDIO_CACHE.complete_capture(
            mgr, first["speech_id"], "voice-signature"
        )

        mgr.send_cached_speech_batch = AsyncMock(return_value=(True, False))
        replay = await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "缓存结束帧失败",
            metadata=_soccer_mirror_meta({"kind": "cache-done-failure"}),
            mirror_text=False,
            emit_turn_end_after=False,
            reuse_synthesized_audio=True,
        )

        assert replay["method"] == "project_tts_cache"
        assert replay["ok"] is False
        assert replay["audio_sent"] is False
        # A cache hit only writes chunks to the socket, so completion is never
        # observed on this path and must not be claimed either way. Delivery is
        # reported by audio_sent; the sibling non-cache path likewise answers
        # None whenever completion was not awaited.
        assert replay["audio_completed"] is None
        mgr.send_cached_speech_batch.assert_awaited_once_with(ANY, replay["speech_id"])
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_waits_for_matching_audio_done_before_returning():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True

    task = asyncio.create_task(core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "等语音完整结束",
        metadata=_soccer_mirror_meta({"kind": "completion-wait"}),
        mirror_text=False,
        emit_turn_end_after=False,
        wait_for_audio_completion=True,
        audio_completion_timeout=1.0,
    ))
    for _ in range(20):
        if getattr(mgr, "_game_speech_completion_waiter", None):
            break
        await asyncio.sleep(0)

    slot = mgr._game_speech_completion_waiter
    assert slot is not None
    assert task.done() is False
    speech_id = mgr.tts_request_queue.messages[0][0]
    assert slot[0] == speech_id

    core_module.LLMSessionManager._resolve_game_speech_completion_wait(
        mgr, speech_id, True
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert result["ok"] is True
    assert result["audio_completed"] is True
    assert getattr(mgr, "_game_speech_completion_waiter", None) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_timeout_clears_pipeline_before_returning():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._clear_tts_pipeline = AsyncMock()

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "超时后清理旧语音",
        metadata=_soccer_mirror_meta({"kind": "completion-timeout"}),
        mirror_text=False,
        emit_turn_end_after=False,
        wait_for_audio_completion=True,
        audio_completion_timeout=0.01,
    )

    assert result["ok"] is False
    assert result["reason"] == "audio_completion_timeout"
    assert result["audio_completed"] is False
    mgr._clear_tts_pipeline.assert_awaited_once()
    assert mgr._game_speech_completion_waiter is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_cancellation_after_queueing_clears_pipeline():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._clear_tts_pipeline = AsyncMock()
    mgr.emit_mirror_turn_end = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "取消后不能留下旧语音",
            metadata=_soccer_mirror_meta({"kind": "cancel-after-queue"}),
            mirror_text=False,
            emit_turn_end_after=True,
            wait_for_audio_completion=True,
        )

    mgr._clear_tts_pipeline.assert_awaited_once()
    assert mgr._game_speech_completion_waiter is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_cosyvoice_waits_for_connection_boundary_audio_completion():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._tts_active_provider_key = "local_cosyvoice"
    mgr._clear_tts_pipeline = AsyncMock()

    task = asyncio.create_task(
        core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "本地协议以连接关闭作为完成边界",
            metadata=_soccer_mirror_meta({"kind": "local-completion-capability"}),
            mirror_text=False,
            emit_turn_end_after=False,
            wait_for_audio_completion=True,
            audio_completion_timeout=1.0,
        )
    )
    for _ in range(20):
        if getattr(mgr, "_game_speech_completion_waiter", None):
            break
        await asyncio.sleep(0)

    slot = mgr._game_speech_completion_waiter
    assert slot is not None
    assert task.done() is False
    core_module.LLMSessionManager._resolve_game_speech_completion_wait(
        mgr, slot[0], True
    )
    result = await asyncio.wait_for(task, timeout=1)

    assert result["ok"] is True
    assert result["audio_queued"] is True
    assert result["audio_completed"] is True
    assert result["audio_completion_supported"] is True
    assert getattr(mgr, "_game_speech_completion_waiter", None) is None
    mgr._clear_tts_pipeline.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_tts_does_not_wait_for_an_unsupported_completion_boundary():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._tts_completion_supported = False
    mgr._tts_audio_output_supported = True
    mgr._clear_tts_pipeline = AsyncMock()

    result = await asyncio.wait_for(
        core_module.LLMSessionManager.mirror_assistant_speech(
            mgr,
            "禁用语音时立即返回",
            metadata=_soccer_mirror_meta({"kind": "disabled-tts"}),
            mirror_text=False,
            emit_turn_end_after=False,
            wait_for_audio_completion=True,
            audio_completion_timeout=45.0,
        ),
        timeout=0.5,
    )

    assert result["ok"] is True
    assert result["audio_queued"] is True
    assert result["audio_completed"] is None
    assert result["audio_completion_supported"] is False
    assert getattr(mgr, "_game_speech_completion_waiter", None) is None
    mgr._clear_tts_pipeline.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("configured_url", "expected"),
    [
        ("", False),
        ("http://127.0.0.1:9880", False),
        ("ws://127.0.0.1:9880", True),
        ("WSS://example.test/cosy", True),
    ],
)
def test_local_cosyvoice_completion_requires_a_websocket_url(configured_url, expected):
    mgr = _make_manager()

    supported = core_module.LLMSessionManager._tts_worker_supports_completion(
        mgr,
        object(),
        "local_cosyvoice",
        {"base_url": configured_url},
    )

    assert supported is expected


@pytest.mark.unit
def test_local_cosyvoice_completion_uses_the_selected_worker_config():
    mgr = _make_manager()
    mgr._config_manager = SimpleNamespace(
        get_model_api_config=lambda slot: {
            "base_url": "wss://custom.example.test/cosy"
            if slot == "tts_custom"
            else "https://default.example.test/tts"
        },
    )

    assert core_module.LLMSessionManager._tts_worker_supports_completion(
        mgr,
        object(),
        "local_cosyvoice",
        mgr._config_manager.get_model_api_config("tts_default"),
    ) is False
    assert core_module.LLMSessionManager._tts_worker_supports_completion(
        mgr,
        object(),
        "local_cosyvoice",
        mgr._config_manager.get_model_api_config("tts_custom"),
    ) is True


@pytest.mark.unit
def test_resolve_tts_worker_spec_returns_the_selected_route_config(monkeypatch):
    mgr = _make_manager()
    default_config = {
        "base_url": "https://default.example.test/tts",
        "api_key": "default-key",
    }
    custom_config = {
        "base_url": "wss://custom.example.test/cosy",
        "api_key": "custom-key",
    }
    mgr.voice_id = "default-voice"
    mgr.core_api_type = "openai"
    mgr._tts_excluded_provider_keys = frozenset()
    mgr._config_manager = SimpleNamespace(
        get_core_config=lambda: {"DISABLE_TTS": False},
        get_model_api_config=lambda slot: (
            custom_config if slot == "tts_custom" else default_config
        ),
    )
    mgr._effective_tts_route = lambda: ("default-voice", False)
    selected_worker = object()
    monkeypatch.setattr(
        tts_runtime_module._core_facade,
        "get_tts_worker",
        lambda **_kwargs: (selected_worker, None, "local_cosyvoice"),
    )

    resolved = core_module.LLMSessionManager._resolve_tts_worker_spec(mgr)

    assert resolved == (
        selected_worker,
        "default-key",
        "default-voice",
        "local_cosyvoice",
        False,
        default_config,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unavailable_tts_worker_returns_an_explicit_failure():
    mgr = _make_manager()
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_ready = True
    mgr._tts_completion_supported = False
    mgr._tts_audio_output_supported = False

    result = await core_module.LLMSessionManager.mirror_assistant_speech(
        mgr,
        "worker 不可用时不应报告成功",
        metadata=_soccer_mirror_meta({"kind": "tts-unavailable"}),
        mirror_text=False,
        emit_turn_end_after=False,
        wait_for_audio_completion=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "tts_unavailable"
    assert result["audio_queued"] is False
    assert result["audio_completion_supported"] is False
    assert mgr.tts_request_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_tts_teardown_preserves_new_runtime_speech_state():
    mgr = _make_manager()
    old_request_queue = _FakeQueue()
    old_response_queue = _FakeQueue()
    new_response_queue = _FakeQueue()
    mgr.tts_response_queue = new_response_queue
    completion = core_module.LLMSessionManager._begin_game_speech_completion_wait(
        mgr,
        "new-runtime-speech",
    )
    core_module.LLMSessionManager._remember_game_speech_correlation(
        mgr,
        "new-runtime-speech",
        "new-runtime-correlation",
    )

    await core_module.LLMSessionManager._teardown_tts_runtime(
        mgr,
        None,
        None,
        old_request_queue,
        old_response_queue,
    )

    assert mgr._game_speech_completion_waiter == (
        "new-runtime-speech",
        completion,
    )
    assert mgr._game_speech_correlation == (
        "new-runtime-speech",
        "new-runtime-correlation",
    )
    assert completion.done() is False

    await core_module.LLMSessionManager._teardown_tts_runtime(
        mgr,
        None,
        None,
        mgr.tts_request_queue,
        new_response_queue,
    )

    assert await completion is False
    assert mgr._game_speech_completion_waiter is None
    assert mgr._game_speech_correlation is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tts_audio_done_resolves_game_speech_completion_slot():
    mgr = _make_manager()
    mgr.tts_response_queue = queue.Queue()
    mgr.current_game_speech_audio_runtime_signature = lambda: "voice-signature"
    mgr.send_audio_done = AsyncMock(return_value=True)
    future = core_module.LLMSessionManager._begin_game_speech_completion_wait(
        mgr, "game-speech-1"
    )
    mgr.tts_response_queue.put(("__audio_done__", "game-speech-1"))

    task = asyncio.create_task(core_module.LLMSessionManager.tts_response_handler(mgr))
    assert await asyncio.wait_for(future, timeout=1) is True
    task.cancel()
    cancelled_result = await asyncio.gather(task, return_exceptions=True)

    assert isinstance(cancelled_result[0], asyncio.CancelledError)
    mgr.send_audio_done.assert_awaited_once_with("game-speech-1")
    assert mgr._game_speech_completion_waiter is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_captures_audio_without_sending_playback():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.send_speech = AsyncMock(return_value=True)
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: (
        "preload-cache-key",
        "preload-runtime-signature",
    )
    mgr.current_game_speech_audio_runtime_signature = (
        lambda: "preload-runtime-signature"
    )

    def fake_worker(request_queue, response_queue, _api_key, _voice_id):
        response_queue.put(("__ready__", True))
        active_speech_id = None
        while True:
            speech_id, text = request_queue.get()
            if speech_id == "__shutdown__":
                return
            if speech_id is None:
                if active_speech_id:
                    response_queue.put(("__audio_done__", active_speech_id))
                    active_speech_id = None
                continue
            active_speech_id = speech_id
            response_queue.put(("__audio__", speech_id, b"silent-preload-pcm"))

    mgr._resolve_tts_worker_spec = lambda: (
        fake_worker,
        "",
        "voice",
        None,
        False,
        {},
    )
    try:
        result = await core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["  预载这句  ", "预载这句"],
        )

        assert result["ok"] is True
        assert result["results"] == [{"index": 0, "status": "loaded"}]
        assert GAME_SPEECH_AUDIO_CACHE.get("preload-cache-key") == (
            b"silent-preload-pcm",
        )
        mgr.send_speech.assert_not_awaited()
        assert mgr.sent_responses == []
        assert mgr.sync_message_queue.messages == []
        assert mgr._game_speech_preload_active_workers == {}

        def must_not_resolve_worker():
            raise AssertionError("a fully cached preload must not start a TTS worker")

        mgr._resolve_tts_worker_spec = must_not_resolve_worker
        cached = await core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["预载这句"],
        )
        assert cached["ok"] is True
        assert cached["results"] == [{"index": 0, "status": "hit"}]
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_keys_on_its_own_locale_and_survives_a_mid_batch_switch():
    """A preload batch owns its cache identity from precompute to completion.

    ``game_speech_audio_cache_identity`` derives the signature from a mutable
    session field, and a batch holds its lock across seconds of synthesis. Both
    halves are asserted here: the request locale reaches the identity call
    instead of being written onto the shared session, and a locale switch
    landing mid-batch cannot discard audio the batch already paid to
    synthesize.
    """
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.send_speech = AsyncMock(return_value=True)
    mgr._conversation_render_language = "Chinese"
    seen_languages = []

    def identity(_text, *, render_language=""):
        seen_languages.append(render_language)
        language = render_language or mgr._conversation_render_language
        return f"preload-key-{language}", f"preload-signature-{language}"

    mgr.game_speech_audio_cache_identity = identity
    mgr.current_game_speech_audio_runtime_signature = (
        lambda: f"preload-signature-{mgr._conversation_render_language}"
    )

    def fake_worker(request_queue, response_queue, _api_key, _voice_id):
        response_queue.put(("__ready__", True))
        active_speech_id = None
        while True:
            speech_id, _text = request_queue.get()
            if speech_id == "__shutdown__":
                return
            if speech_id is None:
                if active_speech_id:
                    # Someone switches the chat language while this batch is
                    # still synthesizing -- a second preload, a speak, or the
                    # user changing the UI language.
                    mgr._conversation_render_language = "English"
                    response_queue.put(("__audio_done__", active_speech_id))
                    active_speech_id = None
                continue
            active_speech_id = speech_id
            response_queue.put(("__audio__", speech_id, b"silent-preload-pcm"))

    mgr._resolve_tts_worker_spec = lambda: (
        fake_worker,
        "",
        "voice",
        None,
        False,
        {},
    )
    try:
        result = await core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["预载这句"],
            render_language="Japanese",
        )

        # Keyed on what the request asked for, not on the session's "Chinese".
        assert seen_languages == ["Japanese"]
        assert mgr._conversation_render_language == "English"
        assert result["ok"] is True
        assert result["results"] == [{"index": 0, "status": "loaded"}]
        assert GAME_SPEECH_AUDIO_CACHE.get("preload-key-Japanese") == (
            b"silent-preload-pcm",
        )
        assert GAME_SPEECH_AUDIO_CACHE.get("preload-key-Chinese") is None
        assert GAME_SPEECH_AUDIO_CACHE.get("preload-key-English") is None
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_propagates_real_cancellation_and_releases_worker():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: (
        "cancel-cache-key",
        "cancel-runtime-signature",
    )
    mgr.current_game_speech_audio_runtime_signature = (
        lambda: "cancel-runtime-signature"
    )

    def blocking_worker(request_queue, response_queue, _api_key, _voice_id):
        response_queue.put(("__ready__", True))
        while True:
            speech_id, _text = request_queue.get()
            if speech_id == "__shutdown__":
                return

    mgr._resolve_tts_worker_spec = lambda: (
        blocking_worker,
        "",
        "voice",
        None,
        False,
        {},
    )
    task = asyncio.create_task(
        core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["取消预载"],
        )
    )
    try:
        for _ in range(100):
            if getattr(mgr, "_game_speech_preload_active_workers", None):
                break
            await asyncio.sleep(0.01)
        assert mgr._game_speech_preload_active_workers

        # A real cancellation must NOT be converted into a normal return value:
        # the request task asked this coroutine to stop, and reporting a result
        # instead makes it effectively uncancellable. Only the internal
        # supersede/teardown signal is absorbed (covered by the test below).
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Cancelling still has to release everything the batch owned.
        assert mgr._game_speech_preload_pending_batches == 0
        assert mgr._game_speech_preload_active_workers == {}
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 0
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_supersede_returns_cancelled_without_killing_the_caller():
    """The internal supersede signal is the one that yields a normal result.

    Route teardown and a superseding batch bump the cancel epoch rather than
    cancelling the request task, so this path must resolve to a plain
    ``cancelled`` result while still releasing the isolated worker.
    """
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: (
        "supersede-cache-key",
        "supersede-runtime-signature",
    )
    mgr.current_game_speech_audio_runtime_signature = (
        lambda: "supersede-runtime-signature"
    )

    def blocking_worker(request_queue, response_queue, _api_key, _voice_id):
        response_queue.put(("__ready__", True))
        while True:
            speech_id, _text = request_queue.get()
            if speech_id == "__shutdown__":
                return

    mgr._resolve_tts_worker_spec = lambda: (
        blocking_worker, "", "voice", None, False, {},
    )
    task = asyncio.create_task(
        core_module.LLMSessionManager.preload_game_speech_audio(mgr, ["被顶替的预载"])
    )
    try:
        for _ in range(100):
            if getattr(mgr, "_game_speech_preload_active_workers", None):
                break
            await asyncio.sleep(0.01)
        assert mgr._game_speech_preload_active_workers

        mgr.cancel_game_speech_preloads()
        result = await task

        assert result == {"ok": False, "reason": "cancelled", "results": []}
        assert mgr._game_speech_preload_pending_batches == 0
        assert mgr._game_speech_preload_active_workers == {}
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 0
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_rejects_worker_without_completion_before_starting_thread():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr.game_speech_audio_cache_identity = lambda _text, **_kwargs: (
        "unavailable-cache-key",
        "unavailable-runtime-signature",
    )
    worker_started = False

    def must_not_start(_request_queue, _response_queue, _api_key, _voice_id):
        nonlocal worker_started
        worker_started = True

    mgr._resolve_tts_worker_spec = lambda: (
        must_not_start,
        "",
        "voice",
        "local_cosyvoice",
        False,
        {"base_url": "http://127.0.0.1:9880"},
    )
    try:
        result = await core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["unsupported preload"],
        )

        assert result == {
            "ok": False,
            "results": [{
                "index": 0,
                "status": "failed",
                "reason": "tts_unavailable",
            }],
            "loaded": 0,
            "hits": 0,
            "failed": 1,
            "reason": "tts_unavailable",
        }
        assert worker_started is False
        assert mgr._game_speech_preload_active_workers == {}
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_speech_preload_retires_legacy_worker_after_item_timeout():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    mgr._game_speech_preload_item_timeout_seconds = 0.05
    mgr.game_speech_audio_cache_identity = lambda text, **_kwargs: (
        f"legacy-timeout:{text}",
        "legacy-timeout-signature",
    )
    mgr.current_game_speech_audio_runtime_signature = (
        lambda: "legacy-timeout-signature"
    )
    requested_speech_ids = []

    def late_raw_worker(request_queue, response_queue, _api_key, _voice_id):
        response_queue.put(("__ready__", True))
        active_speech_id = None
        first_item = True
        while True:
            speech_id, _text = request_queue.get()
            if speech_id == "__shutdown__":
                return
            if speech_id is not None:
                active_speech_id = speech_id
                requested_speech_ids.append(speech_id)
                continue
            if not active_speech_id:
                continue
            if first_item:
                first_item = False
                time.sleep(0.5)
                response_queue.put(b"late-untagged-first-item")
            else:
                response_queue.put(b"second-item-audio")
            response_queue.put(("__audio_done__", active_speech_id))
            active_speech_id = None

    mgr._resolve_tts_worker_spec = lambda: (
        late_raw_worker,
        "",
        "voice",
        None,
        False,
        {},
    )
    try:
        result = await core_module.LLMSessionManager.preload_game_speech_audio(
            mgr,
            ["first", "second"],
        )

        assert result["ok"] is False
        assert result["results"] == [
            {"index": 0, "status": "failed", "reason": "timeout"},
            {
                "index": 1,
                "status": "failed",
                "reason": "tts_worker_reset_required",
            },
        ]
        assert len(requested_speech_ids) == 1
        assert GAME_SPEECH_AUDIO_CACHE.get("legacy-timeout:second") is None
        assert mgr._game_speech_preload_active_workers == {}
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 0
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_tts_handler_cancellation_preserves_new_runtime_cache_capture():
    GAME_SPEECH_AUDIO_CACHE.clear()
    mgr = _make_manager()
    old_response_queue = queue.Queue()
    mgr.tts_response_queue = old_response_queue
    mgr._start_tts_response_handler()
    old_handler = mgr.tts_handler_task
    await asyncio.sleep(0)

    mgr.tts_response_queue = queue.Queue()
    assert GAME_SPEECH_AUDIO_CACHE.begin_capture(
        mgr,
        "new-runtime-speech",
        "new-runtime-cache-key",
        "new-runtime-signature",
    ) is True
    try:
        await core_module.LLMSessionManager._stop_tts_response_handler(mgr)

        assert old_handler.done()
        assert GAME_SPEECH_AUDIO_CACHE.stats()["captures"] == 1
    finally:
        GAME_SPEECH_AUDIO_CACHE.clear()


@pytest.mark.unit
def test_game_speech_audio_identity_is_opaque_and_invalidates_by_language():
    mgr = _make_manager()
    mgr._build_tts_runtime_key = lambda: ("provider", "secret-api-key", "voice-a")
    mgr._conversation_render_language = "zh-CN"

    key_zh, signature_zh = core_module.LLMSessionManager.game_speech_audio_cache_identity(
        mgr, "秘密台词"
    )
    mgr._conversation_render_language = "ja-JP"
    key_ja, signature_ja = core_module.LLMSessionManager.game_speech_audio_cache_identity(
        mgr, "秘密台词"
    )

    assert len(key_zh) == len(signature_zh) == 64
    assert "秘密台词" not in key_zh
    assert "secret-api-key" not in signature_zh
    assert key_zh != key_ja
    assert signature_zh != signature_ja


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

    assert result["ok"] is False
    assert result["reason"] == "tts_unavailable"
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

    assert result["ok"] is False
    assert result["reason"] == "tts_unavailable"
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
async def test_mirror_user_input_propagates_the_source_route_identity():
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()

    await core_module.LLMSessionManager.mirror_user_input(
        mgr,
        "source-bound transcript",
        metadata={
            "source": "external_voice_route",
            "kind": "example-game",
            "session_id": "reused-session",
            "sdk_route_instance_id": "route-A",
        },
        request_id="voice-route-a",
        input_type="mirror_voice_transcript",
        send_to_frontend=True,
    )

    assert mgr.websocket.sent == [{
        "type": "user_transcript",
        "text": "source-bound transcript",
        "source": "external_voice_route",
        "request_id": "voice-route-a",
        "game_type": "example-game",
        "session_id": "reused-session",
        "sdk_route_instance_id": "route-A",
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_ownership_mismatch_drop_is_logged(monkeypatch):
    """The most total drop in the voice path must not be the quietest one.

    A transcript whose ingress identity does not match the live route is
    refused above the takeover dispatcher AND above every recording path, so it
    leaves no trace: not in the game, not in chat, not in the logs. This code
    has already been reworked several times between the two failure directions
    -- "dropped a sentence" and "bound one to the wrong route" -- and with no
    log there is nothing afterwards to tell them apart.

    Captured off the module logger rather than through ``caplog``: these loggers
    do not propagate to root, so a caplog-based assertion would pass vacuously
    the moment the log line was deleted.
    """
    mgr = _make_transcript_manager()
    mgr._broadcast_voice_transcript_observed = AsyncMock()
    monkeypatch.setattr(
        turn_module,
        "get_active_game_route_generation_identity",
        lambda _lanlan_name: ("example-game", "session-b", "route-B"),
    )
    logged = []
    monkeypatch.setattr(
        turn_module.logger,
        "info",
        lambda message, *args: logged.append(message % args if args else message),
    )

    handled = await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "  words from the previous route  ",
        is_voice_source=True,
        source_game_route_identity=("example-game", "session-a", "route-A"),
    )

    assert handled is False
    # Nothing recorded it anywhere else -- which is exactly why the log matters.
    assert mgr.sync_message_queue.messages == []
    assert mgr._activity_tracker.user_messages == []
    dropped = [line for line in logged if "route ownership" in line]
    assert len(dropped) == 1, logged
    # Both identities, so an incident can be lined up without the text.
    assert "route-A" in dropped[0] and "route-B" in dropped[0]
    assert "words from the previous route" not in dropped[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_ownership_match_is_not_logged_as_a_drop(monkeypatch):
    """The control: a matching identity must not produce the same line."""
    mgr = _make_transcript_manager()
    mgr._broadcast_voice_transcript_observed = AsyncMock()
    monkeypatch.setattr(
        turn_module,
        "get_active_game_route_generation_identity",
        lambda _lanlan_name: ("example-game", "session-a", "route-A"),
    )
    logged = []
    monkeypatch.setattr(
        turn_module.logger,
        "info",
        lambda message, *args: logged.append(message % args if args else message),
    )

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "  words from the live route  ",
        is_voice_source=True,
        source_game_route_identity=("example-game", "session-a", "route-A"),
    )

    assert not [line for line in logged if "route ownership" in line]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_omni_realtime_transcript_freezes_source_route_identity_before_await(
    monkeypatch,
):
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniRealtimeClient)
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._broadcast_voice_transcript_observed = AsyncMock()
    route_identity = ["example-game", "reused-session", "route-A"]
    monkeypatch.setattr(
        turn_module,
        "get_active_game_route_generation_identity",
        lambda _lanlan_name: tuple(route_identity),
    )

    async def replace_route_during_takeover_probe(*_args, **_kwargs):
        route_identity[:] = ["example-game", "reused-session", "route-B"]
        return False

    mgr._takeover_input_dispatcher = replace_route_during_takeover_probe
    await core_module.LLMSessionManager.handle_input_transcript(
        mgr,
        "route-bound words",
    )

    assert mgr.websocket.sent == [{
        "type": "user_transcript",
        "text": "route-bound words",
        "game_type": "example-game",
        "session_id": "reused-session",
        "sdk_route_instance_id": "route-A",
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
    assert mgr.last_user_engagement_time is not None
    mgr._publish_user_utterance_to_plugin_bus.assert_not_called()
    assert mgr.sync_message_queue.messages == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_takeover_dispatcher_receives_voice_echo_match_before_suppression(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    assert mgr.last_user_engagement_time == FIXED_TS
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
@pytest.mark.parametrize("input_type", ["screen", "camera"])
async def test_text_mode_live_vision_input_is_mirrored_without_engagement(
    monkeypatch,
    input_type,
):
    """Automatic vision frames remain analyzable but are not user engagement."""
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
        {"input_type": input_type, "data": "raw-image"},
    )

    mgr.session.stream_image.assert_awaited_once_with("img-b64")
    assert mgr.sync_message_queue.messages == [{
        "type": "user",
        "data": {
            "input_type": input_type,
            "data": "data:image/jpeg;base64,img-b64",
            "has_image": True,
            "mime_type": "image/jpeg",
        },
    }]
    assert mgr.last_user_engagement_time is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["screen", "camera"])
async def test_voice_live_vision_input_preserves_source_and_request_identity(
    monkeypatch,
    input_type,
):
    """Realtime staging must keep enough metadata to bind a frame to its owner."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniRealtimeClient)
    mgr.session.ws = object()
    mgr.session.stream_image = AsyncMock(
        return_value=MagicMock(
            accepted=True,
            mode="external_description",
            generation=17,
        )
    )
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    monkeypatch.setattr(
        core_module,
        "process_screen_data",
        AsyncMock(return_value="img-b64"),
    )

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {
            "input_type": input_type,
            "data": "raw-image",
            "request_id": "req-vision-17",
        },
    )

    mgr.session.stream_image.assert_awaited_once_with(
        "img-b64",
        source=input_type,
        request_id="req-vision-17",
        captured_at=ANY,
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["screen", "camera"])
@pytest.mark.parametrize(
    ("response_backend", "session_type", "stages_adapter_cache"),
    [
        ("realtime", core_module.OmniRealtimeClient, True),
        ("offline_vlm", core_module.OmniOfflineClient, False),
    ],
)
async def test_independent_asr_live_vision_stays_out_of_provider_queues(
    monkeypatch,
    input_type,
    response_backend,
    session_type,
    stages_adapter_cache,
):
    """Independent live frames remain Core-owned across backend promotion."""
    mgr = _make_manager()
    session = object.__new__(session_type)
    session.stream_image = AsyncMock()
    session._pending_images = ["existing-user-attachment"]
    if stages_adapter_cache:
        session.stage_multimodal_frame = MagicMock()
    mgr.session = session
    mgr.response_backend = response_backend
    mgr._asr_route_mode = "independent"
    mgr._stage_independent_visual_frame = MagicMock(return_value=True)
    mgr.is_goodbye_silent = Mock(return_value=False)
    mgr.is_active = True
    mgr.session_ready = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._pending_input_flush_active = False
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    monkeypatch.setattr(
        core_module,
        "process_screen_data",
        AsyncMock(return_value="img-b64"),
    )

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {
            "input_type": input_type,
            "data": "raw-image",
            "request_id": "req-independent-vision",
        },
    )

    mgr._stage_independent_visual_frame.assert_called_once_with(
        "img-b64",
        source=input_type,
        request_id="req-independent-vision",
        captured_at=ANY,
    )
    session.stream_image.assert_not_awaited()
    assert session._pending_images == ["existing-user-attachment"]
    assert mgr.pending_input_data == []
    assert mgr.sync_message_queue.messages == []
    if stages_adapter_cache:
        session.stage_multimodal_frame.assert_called_once_with(
            "img-b64",
            source=input_type,
            request_id="req-independent-vision",
            captured_at=ANY,
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["avatar_drop_image", "user_image"])
async def test_voice_session_hands_one_shot_user_images_to_offline_vision(
    monkeypatch,
    input_type,
):
    """Attachments leave voice mode and stage on the text/offline vision path."""
    mgr = _make_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    realtime_session.ws = object()
    realtime_session.stream_image = AsyncMock()
    offline_session = object.__new__(core_module.OmniOfflineClient)
    offline_session.stream_image = AsyncMock()
    mgr.session = realtime_session
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr.input_cache_lock = asyncio.Lock()
    mgr.session_ready = True
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)

    async def _end_session(*, by_server=False, reset_starting_count=True, preserve_pending_input=False):
        assert reset_starting_count is False
        # 就地换 offline 会话时必须保留 pending_input_data：拆 session 的
        # await 窗口里并发缓存进来的用户输入不能被 teardown 顺手清掉。
        assert preserve_pending_input is True
        # 内部就地替换不能给前端推 CHARACTER_LEFT。
        assert by_server is True
        mgr.session = None
        mgr.is_active = False

    async def _start_session(_websocket, *, new=False, input_mode=None):
        assert new is False
        assert input_mode == "text"
        mgr.session = offline_session
        mgr.is_active = True
        mgr.session_ready = True

    mgr.end_session = AsyncMock(side_effect=_end_session)
    mgr.start_session = AsyncMock(side_effect=_start_session)
    validate = AsyncMock(return_value="img-b64")
    monkeypatch.setattr(core_module, "process_screen_data", validate)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {
            "input_type": input_type,
            "data": "raw-image",
            "request_id": "req-one-shot",
        },
    )

    realtime_session.stream_image.assert_not_awaited()
    validate.assert_awaited_once_with("raw-image")
    offline_session.stream_image.assert_awaited_once_with("img-b64")
    mgr.end_session.assert_awaited_once_with(
        by_server=True,
        reset_starting_count=False,
        preserve_pending_input=True,
    )
    mgr.start_session.assert_awaited_once_with(
        mgr.websocket,
        new=False,
        input_mode="text",
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["avatar_drop_image", "user_image"])
async def test_invalid_one_shot_image_does_not_destroy_voice_session(
    monkeypatch,
    input_type,
):
    mgr = _make_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    realtime_session.ws = object()
    mgr.session = realtime_session
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr.end_session = AsyncMock()
    mgr.start_session = AsyncMock()
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr(core_module, "process_screen_data", validate)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": input_type, "data": "invalid-image"},
    )

    validate.assert_awaited_once_with("invalid-image")
    mgr.end_session.assert_not_awaited()
    mgr.start_session.assert_not_awaited()
    assert mgr.session is realtime_session
    assert mgr.is_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_text_does_not_destroy_voice_session():
    mgr = _make_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    mgr.session = realtime_session
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr.end_session = AsyncMock()
    mgr.start_session = AsyncMock()

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": {"not": "text"}},
    )

    mgr.end_session.assert_not_awaited()
    mgr.start_session.assert_not_awaited()
    assert mgr.session is realtime_session
    assert mgr.is_active is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attachment_stages_before_inputs_cached_during_offline_handoff(
    monkeypatch,
):
    mgr = _make_transcript_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    offline_session = object.__new__(core_module.OmniOfflineClient)
    offline_session._pending_images = []
    offline_session.update_max_response_length = Mock()
    delivery_order = []

    async def _stream_image(image_b64):
        assert mgr.session is offline_session
        delivery_order.append(("image", image_b64))

    async def _stream_text(text, **_kwargs):
        assert mgr.session is offline_session
        delivery_order.append(("text", text))

    offline_session.stream_image = AsyncMock(side_effect=_stream_image)
    offline_session.stream_text = AsyncMock(side_effect=_stream_text)
    mgr.session = realtime_session
    mgr.is_active = True
    mgr.session_ready = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=False)
    mgr.agent_flags = {}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()

    async def _end_session(*, by_server=False, reset_starting_count=True, preserve_pending_input=False):
        assert reset_starting_count is False
        # 就地换 offline 会话时必须保留 pending_input_data：拆 session 的
        # await 窗口里并发缓存进来的用户输入不能被 teardown 顺手清掉。
        assert preserve_pending_input is True
        # 内部就地替换不能给前端推 CHARACTER_LEFT。
        assert by_server is True
        mgr.session = None
        mgr.is_active = False

    async def _start_session(_websocket, *, new=False, input_mode=None):
        assert new is False
        assert input_mode == "text"
        mgr.session = offline_session
        mgr.is_active = True
        mgr.session_ready = True
        mgr.pending_input_data.append(
            {"input_type": "text", "data": "describe this image"}
        )
        await core_module.LLMSessionManager._flush_pending_input_data(mgr)
        assert delivery_order == []

    mgr.end_session = AsyncMock(side_effect=_end_session)
    mgr.start_session = AsyncMock(side_effect=_start_session)
    monkeypatch.setattr(
        core_module,
        "process_screen_data",
        AsyncMock(return_value="img-b64"),
    )
    monkeypatch.setattr(
        core_module,
        "dispatch_text_user_message",
        lambda _name, _text: None,
    )

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "avatar_drop_image", "data": "raw-image"},
    )

    assert delivery_order == [
        ("image", "img-b64"),
        ("text", "describe this image"),
    ]
    assert mgr.pending_input_data == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_initiating_text_submits_before_inputs_cached_during_handoff(
    monkeypatch,
):
    """The message that triggered the handoff must speak first.

    end_session now preserves inputs cached during teardown, and start_session
    flushes that queue before returning. Without the same owner-before-flush
    deferral the one-shot attachments use, a text that arrived DURING teardown
    would enter history and generate first, and the older initiating message
    would then interrupt it — the user's two turns come out reversed.
    """
    mgr = _make_transcript_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    offline_session = object.__new__(core_module.OmniOfflineClient)
    offline_session._pending_images = []
    offline_session.update_max_response_length = Mock()
    delivery_order = []

    async def _stream_text(text, **_kwargs):
        assert mgr.session is offline_session
        delivery_order.append(text)

    offline_session.stream_text = AsyncMock(side_effect=_stream_text)
    mgr.session = realtime_session
    mgr.is_active = True
    mgr.session_ready = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=False)
    mgr.agent_flags = {}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()

    async def _end_session(*, by_server=False, reset_starting_count=True, preserve_pending_input=False):
        assert preserve_pending_input is True
        assert by_server is True
        mgr.session = None
        mgr.is_active = False

    async def _start_session(_websocket, *, new=False, input_mode=None):
        assert input_mode == "text"
        mgr.session = offline_session
        mgr.is_active = True
        mgr.session_ready = True
        # 拆 session 期间到达的第二条消息被保留了下来。
        mgr.pending_input_data.append(
            {"input_type": "text", "data": "second message"}
        )
        await core_module.LLMSessionManager._flush_pending_input_data(mgr)
        # 发起本次 handoff 的那条还没提交，缓存的这条必须被挡住。
        assert delivery_order == []

    mgr.end_session = AsyncMock(side_effect=_end_session)
    mgr.start_session = AsyncMock(side_effect=_start_session)
    monkeypatch.setattr(
        core_module,
        "dispatch_text_user_message",
        lambda _name, _text: None,
    )

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "first message"},
    )

    assert delivery_order == ["first message", "second message"]
    assert mgr.pending_input_data == []


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["avatar_drop_image", "user_image"])
async def test_one_shot_user_image_records_engagement(
    monkeypatch,
    input_type,
):
    """Accepted user images preserve arrival time across asynchronous staging."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    clock = {"now": FIXED_TS + 25.0}

    async def _process_after_clock_advance(_data):
        clock["now"] = FIXED_TS + 50.0
        return "img-b64"

    monkeypatch.setattr(core_module, "process_screen_data", _process_after_clock_advance)
    # ingress 时间戳取自 main_logic.core.streaming._user_input_ingress_time，
    # 门面 core_module 自己不读时钟。
    patch_module_clock(monkeypatch, streaming_module, time=lambda: clock["now"])

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {
            "input_type": input_type,
            "data": "raw-image",
            "_user_input_ingress_time": FIXED_TS,
        },
    )

    mgr.session.stream_image.assert_awaited_once_with("img-b64")
    assert mgr.last_user_engagement_time == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["avatar_drop_image", "user_image"])
async def test_cached_user_image_preserves_server_ingress_time(
    monkeypatch,
    input_type,
):
    """Session-start caching must preserve a user image's server arrival time."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    clock = {"now": FIXED_TS}
    # 同上：ingress 时间戳来自 main_logic.core.streaming。
    patch_module_clock(monkeypatch, streaming_module, time=lambda: clock["now"])
    monkeypatch.setattr(
        core_module,
        "process_screen_data",
        AsyncMock(return_value="img-b64"),
    )

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {"input_type": input_type, "data": "raw-image"},
    )

    assert mgr.pending_input_data[0]["_user_input_ingress_time"] == FIXED_TS
    clock["now"] = FIXED_TS + 50.0
    mgr._starting_session_count = 0
    mgr.session_ready = True
    await core_module.LLMSessionManager._flush_pending_input_data(mgr)

    mgr.session.stream_image.assert_awaited_once_with("img-b64")
    assert mgr.last_user_engagement_time == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_data_preserves_router_stamped_text_ingress(monkeypatch):
    """Task startup must not overwrite the timestamp sampled by the WS router."""
    mgr = _make_manager()
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    # _stream_data_now 的 fallback 采样点在 main_logic.core.streaming。
    patch_module_clock(
        monkeypatch,
        streaming_module,
        time=lambda: FIXED_TS + 50.0,
    )

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {
            "input_type": "text",
            "data": "arrived before task start",
            "_user_input_ingress_time": FIXED_TS,
        },
    )

    assert mgr.pending_input_data[0]["_user_input_ingress_time"] == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_type", "data"),
    [
        ("text", "arrived while startup is circuit-broken"),
        ("avatar_drop_image", "raw-image"),
        ("user_image", "raw-image"),
    ],
)
async def test_one_shot_input_records_engagement_before_startup_failure(
    input_type,
    data,
):
    """Fallible session startup cannot erase genuine input engagement."""
    mgr = _make_transcript_manager()
    mgr.session = None
    mgr.is_active = False
    mgr.session_ready = False
    mgr._starting_session_count = 0
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._session_start_circuit_open = True
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr.last_user_engagement_time = None

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {
            "input_type": input_type,
            "data": data,
            "_user_input_ingress_time": FIXED_TS,
        },
    )

    assert mgr.last_user_engagement_time == FIXED_TS


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
async def test_cached_text_preserves_server_ingress_time(monkeypatch):
    """Session-start caching must not move engagement past later proactive output."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=True)
    mgr.agent_flags = {"openclaw_enabled": True, "openclaw_ready": False}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()
    clock = {"now": FIXED_TS}
    # 同上：文本 ingress / last_user_*_time 都在 main_logic.core.streaming 采样。
    patch_module_clock(monkeypatch, streaming_module, time=lambda: clock["now"])
    monkeypatch.setattr(
        core_module,
        "dispatch_text_user_message",
        lambda name, text: None,
    )

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {"input_type": "text", "data": "/openclaw stop", "request_id": "req-1"},
    )

    assert mgr.pending_input_data[0]["_user_input_ingress_time"] == FIXED_TS
    clock["now"] = FIXED_TS + 50.0
    mgr._starting_session_count = 0
    mgr.session_ready = True
    await core_module.LLMSessionManager._flush_pending_input_data(mgr)

    assert mgr.last_user_activity_time == FIXED_TS
    assert mgr.last_user_message_time == FIXED_TS
    assert mgr.last_user_engagement_time == FIXED_TS

    mgr.last_user_activity_time = FIXED_TS + 100.0
    mgr.last_user_message_time = FIXED_TS + 100.0
    mgr.last_user_engagement_time = FIXED_TS + 100.0
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {
            "input_type": "text",
            "data": "older request resumed",
            "_user_input_ingress_time": FIXED_TS,
        },
    )

    assert mgr.last_user_activity_time == FIXED_TS + 100.0
    assert mgr.last_user_message_time == FIXED_TS + 100.0
    assert mgr.last_user_engagement_time == FIXED_TS + 100.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_text_dropped_for_voice_still_records_engagement():
    """A typed response remains engagement even when voice startup discards it."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniRealtimeClient)
    mgr.is_active = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = [
        {
            "input_type": "text",
            "data": "我在这里",
            "_user_input_ingress_time": FIXED_TS,
        }
    ]
    mgr.last_user_engagement_time = None

    await core_module.LLMSessionManager._flush_pending_input_data(mgr)

    assert mgr.pending_input_data == []
    assert mgr.last_user_engagement_time == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_live_text_queues_behind_pending_input_flush():
    """Live input must not overtake the batch currently being replayed."""
    mgr = _make_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.is_active = True
    mgr.session_ready = True
    mgr._starting_session_count = 0
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._pending_input_flush_active = True
    mgr.note_stream_input_ingress = Mock()
    mgr._should_drop_live_vision_stream = Mock(return_value=False)

    await core_module.LLMSessionManager._stream_data_now(
        mgr,
        {"input_type": "text", "data": "new live text"},
    )

    assert len(mgr.pending_input_data) == 1
    assert mgr.pending_input_data[0]["input_type"] == "text"
    assert mgr.pending_input_data[0]["data"] == "new live text"
    assert isinstance(mgr.pending_input_data[0]["_user_input_ingress_time"], float)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["avatar_drop_image", "user_image"])
async def test_cached_user_image_hands_ready_voice_session_to_offline_vision(
    monkeypatch,
    input_type,
):
    """A cached attachment hands its following text to the offline session."""
    mgr = _make_manager()
    realtime_session = object.__new__(core_module.OmniRealtimeClient)
    offline_session = object.__new__(core_module.OmniOfflineClient)
    offline_session.stream_image = AsyncMock()
    mgr.session = realtime_session
    mgr.is_active = True
    mgr.session_ready = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr.pending_input_data = [
        {
            "input_type": input_type,
            "data": "raw-image",
            "_user_input_ingress_time": FIXED_TS,
        },
        {
            "input_type": "text",
            "data": "describe this image",
            "_user_input_ingress_time": FIXED_TS + 1,
        },
    ]
    mgr.last_user_engagement_time = None

    async def _end_session(*, by_server=False, reset_starting_count=True, preserve_pending_input=False):
        assert reset_starting_count is False
        # 就地换 offline 会话时必须保留 pending_input_data：拆 session 的
        # await 窗口里并发缓存进来的用户输入不能被 teardown 顺手清掉。
        assert preserve_pending_input is True
        # 内部就地替换不能给前端推 CHARACTER_LEFT。
        assert by_server is True
        mgr.session = None
        mgr.is_active = False

    async def _start_session(_websocket, *, new=False, input_mode=None):
        assert new is False
        assert input_mode == "text"
        mgr.session = offline_session
        mgr.is_active = True
        mgr.session_ready = True

    mgr.end_session = AsyncMock(side_effect=_end_session)
    mgr.start_session = AsyncMock(side_effect=_start_session)
    validate = AsyncMock(return_value="img-b64")
    monkeypatch.setattr(core_module, "process_screen_data", validate)
    deliver_text = AsyncMock()
    process_pending = core_module.LLMSessionManager._process_stream_data_internal

    async def _process_pending(message):
        if message.get("input_type") == "text":
            await deliver_text(message)
            return
        await process_pending(mgr, message)

    mgr._process_stream_data_internal = AsyncMock(side_effect=_process_pending)

    await core_module.LLMSessionManager._flush_pending_input_data(mgr)

    assert mgr.pending_input_data == []
    assert mgr.last_user_engagement_time == FIXED_TS
    validate.assert_awaited_once_with("raw-image")
    offline_session.stream_image.assert_awaited_once_with("img-b64")
    deliver_text.assert_awaited_once_with(
        {
            "input_type": "text",
            "data": "describe this image",
            "_user_input_ingress_time": FIXED_TS + 1,
        }
    )
    mgr.end_session.assert_awaited_once_with(
        by_server=True,
        reset_starting_count=False,
        preserve_pending_input=True,
    )
    mgr.start_session.assert_awaited_once_with(
        mgr.websocket,
        new=False,
        input_mode="text",
    )


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
async def test_text_stream_discard_callback_keeps_original_request_owner(monkeypatch):
    """A late discard from request A must not clear request B's frontend output."""
    mgr = _make_transcript_manager()
    mgr.session = object.__new__(core_module.OmniOfflineClient)
    mgr.session._pending_images = []
    mgr.session.update_max_response_length = Mock()
    mgr.session.stream_text = AsyncMock()
    mgr.is_active = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=False)
    mgr.agent_flags = {}
    mgr.pending_agent_callbacks = []
    mgr._fire_task = Mock()
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "request A", "request_id": "req-A"},
    )

    discard_callback = mgr.session.stream_text.await_args.kwargs["response_discarded_callback"]
    mgr._active_text_request_id = "req-B"
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._clear_tts_pipeline = AsyncMock()

    await discard_callback("guard", 1, 3, False, None)

    assert mgr.websocket.sent == []
    assert mgr._active_text_request_id == "req-B"
    mgr._clear_tts_pipeline.assert_not_awaited()
    assert {
        "type": "system",
        "data": "response_discarded_clear",
    } not in mgr.sync_message_queue.messages


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_truncated_recovery_does_not_mutate_newer_request_state():
    """Request A's late recovery must not emit or consume request B's turn state.

    Session-level wrap-up is deliberately NOT behind that ownership gate: A's turn
    really did end, and skipping its archive/prewarm accounting is exactly what
    re-opens the "context grows -> keeps truncating and recovering" loop.
    """
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr.session = MagicMock()
    mgr.session._conversation_history = ["request-B-history"]
    mgr._active_text_request_id = "req-B"
    mgr.current_speech_id = "speech-B"
    mgr._pending_turn_meta = {"kind": "text", "request_id": "req-B"}
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        3,
        3,
        False,
        '{"code":"RESPONSE_LENGTH_TRUNCATED","text":"stale response A"}',
        request_id="req-A",
    )

    assert mgr._active_text_request_id == "req-B"
    assert mgr.current_speech_id == "speech-B"
    assert mgr._pending_turn_meta == {"kind": "text", "request_id": "req-B"}
    assert mgr.session._conversation_history == ["request-B-history"]
    assert mgr.sent_responses == []
    mgr._clear_tts_pipeline.assert_not_awaited()
    mgr._emit_turn_end.assert_not_awaited()
    assert mgr.websocket.sent == []
    # Shared-output writes are suppressed, session accounting still runs.
    mgr._finalize_turn_after_emit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_truncated_recovery_stops_when_new_request_starts_during_ui_send():
    """Request A must re-check ownership after yielding to its recovery UI send.

    Losing ownership mid-sequence stops the remaining shared-output steps, but the
    session-level wrap-up still runs — see the sibling stale-recovery test.
    """
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr.session = MagicMock()
    mgr.session._conversation_history = ["history-before-A"]
    mgr._active_text_request_id = "req-A"
    mgr.current_speech_id = "speech-A"
    mgr._pending_turn_meta = {"kind": "text", "request_id": "req-A"}
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()

    async def send_recovery_then_start_request_b(
        text,
        is_first_chunk=False,
        turn_id=None,
        metadata=None,
        **kwargs,
    ):
        mgr.sent_responses.append({
            "text": text,
            "is_first_chunk": is_first_chunk,
            "turn_id": turn_id,
            "metadata": metadata,
            "request_id": kwargs.get("request_id"),
        })
        mgr._active_text_request_id = "req-B"
        mgr.current_speech_id = "speech-B"
        mgr._pending_turn_meta = {"kind": "text", "request_id": "req-B"}
        mgr.session._conversation_history.append("request-B-history")

    mgr.send_lanlan_response = send_recovery_then_start_request_b

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        3,
        3,
        False,
        '{"code":"RESPONSE_LENGTH_TRUNCATED","text":"recovery response A"}',
        request_id="req-A",
    )

    assert mgr._active_text_request_id == "req-B"
    assert mgr.current_speech_id == "speech-B"
    assert mgr._pending_turn_meta == {"kind": "text", "request_id": "req-B"}
    assert mgr.session._conversation_history == [
        "history-before-A",
        "request-B-history",
    ]
    assert mgr.sent_responses == [{
        "text": "recovery response A",
        "is_first_chunk": True,
        "turn_id": "speech-A",
        "metadata": None,
        "request_id": "req-A",
    }]
    mgr._emit_turn_end.assert_not_awaited()
    # Shared-output writes stop at the ownership loss, session accounting still runs.
    mgr._finalize_turn_after_emit.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_owned_truncated_recovery_still_finalizes_when_owner_stays_current():
    """Dynamic ownership checks must not suppress A's normal turn finalization."""
    mgr = _make_manager()
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    mgr._active_text_request_id = "req-A"
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        3,
        3,
        False,
        '{"code":"RESPONSE_LENGTH_TRUNCATED","text":"recovery response A"}',
        request_id="req-A",
    )

    mgr._emit_turn_end.assert_awaited_once_with("req-A")
    mgr._finalize_turn_after_emit.assert_awaited_once()
    assert mgr._active_text_request_id is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unowned_discard_callback_keeps_global_clear_behavior():
    """Legacy/proactive discard callbacks still clear shared output globally."""
    mgr = _make_manager()
    mgr._active_text_request_id = "req-current"
    mgr._clear_tts_pipeline = AsyncMock()

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        1,
        3,
        True,
    )

    mgr._clear_tts_pipeline.assert_awaited_once()
    assert {
        "type": "system",
        "data": "response_discarded_clear",
    } in mgr.sync_message_queue.messages


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
async def test_passive_callback_media_remains_bound_across_concurrent_focus_wait(
    monkeypatch,
):
    """A later text task must not steal an earlier callback's staged images."""
    mgr = _make_transcript_manager()
    offline_session = object.__new__(core_module.OmniOfflineClient)
    offline_session._pending_images = ["user-image"]
    offline_session.update_max_response_length = Mock()
    stream_calls = []

    async def stream_text(text, **kwargs):
        stream_calls.append((text, kwargs))

    offline_session.stream_text = AsyncMock(side_effect=stream_text)
    mgr.session = offline_session
    mgr.is_active = True
    mgr.session_ready = True
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=False)
    mgr.agent_flags = {}
    mgr._fire_task = Mock()
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._push_focus_thinking = AsyncMock()
    mgr.pending_agent_callbacks = [{
        "_callback_delivery_id": "id-focus-race",
        "status": "completed",
        "summary": "callback context",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image-1", "callback-image-2"],
    }]
    first_focus_entered = asyncio.Event()
    release_first_focus = asyncio.Event()

    async def focus_decision(text):
        if text == "first text":
            first_focus_entered.set()
            await release_first_focus.wait()
        return False

    mgr._focus_inline_decision = AsyncMock(side_effect=focus_decision)
    monkeypatch.setattr(
        core_module,
        "dispatch_text_user_message",
        lambda name, text: None,
    )

    first_task = asyncio.create_task(
        core_module.LLMSessionManager._process_stream_data_internal(
            mgr,
            {"input_type": "text", "data": "first text"},
        )
    )
    await first_focus_entered.wait()
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "second text"},
    )
    release_first_focus.set()
    await first_task

    calls_by_text = {text: kwargs for text, kwargs in stream_calls}
    assert "system_prefix_images" not in calls_by_text["second text"]
    assert calls_by_text["first text"]["system_prefix_images"] == [
        "callback-image-1",
        "callback-image-2",
    ]
    assert "callback context" in calls_by_text["first text"]["system_prefix"]
    assert offline_session._pending_images == ["user-image"]
    assert mgr.pending_agent_callbacks == []


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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "今天天气不错", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == FIXED_TS
    assert mgr.last_user_message_time == FIXED_TS
    assert mgr.last_user_engagement_time == FIXED_TS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ai_echo_transcript_does_not_stamp_last_user_message_time(monkeypatch):
    """An AI voice echo is activity, but never a genuine user response."""
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
    mgr._recent_ai_voice_echo_text = "要不要现在跟我一起踢一会儿足球小游戏？"
    mgr._recent_ai_voice_echo_at = FIXED_TS

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "要不要现在跟我一起踢一会儿足球小游戏", is_voice_source=True,
    )

    # 回声照样污染 last_user_activity_time（说明旧字段为何不能用于邀请判定）
    assert mgr.last_user_activity_time == FIXED_TS
    # 但真消息时间戳保持干净
    assert mgr.last_user_message_time is None
    assert mgr.last_user_engagement_time is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_voice_transcript_does_not_stamp_last_user_message_time(monkeypatch):
    """An empty voice transcript is activity, but not a genuine user response."""
    mgr = _make_transcript_manager()
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "   ", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == FIXED_TS
    assert mgr.last_user_message_time is None
    assert mgr.last_user_engagement_time is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_last_user_message_time_uses_transcript_arrival_not_post_await(monkeypatch):
    """Use transcript arrival time without regressing newer engagement.

    A takeover dispatcher may delay normal transcript processing. The message
    timestamp must retain the pre-await arrival time, while a newer interaction
    recorded during that await must remain the latest engagement signal.
    """
    mgr = _make_transcript_manager()
    calls = {"n": 0}

    def _ticking_time():
        calls["n"] += 1
        return 100.0 + calls["n"]

    # 打到真正读时钟的模块上：转写到达时刻取自 main_logic.core.turn 的
    # time.time()，core_module（main_logic.core 门面）自己不读。此前那版
    # `setattr(core_module.time, "time", ...)` 之所以生效，靠的正是它其实
    # replace 了整个 stdlib time 模块——即这条用例一直依赖的是全局副作用。
    patch_module_clock(monkeypatch, turn_module, time=_ticking_time)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda name, text: None)

    async def _dispatcher(name, text, request_id=None):
        turn_module.time.time()  # 模拟 await 期间时钟流逝
        mgr.note_user_engagement(at=200.0)
        return False             # 未处理 → 继续普通流程走到真消息块

    mgr._takeover_input_dispatcher = _dispatcher
    mgr.session = object()

    await core_module.LLMSessionManager.handle_input_transcript(
        mgr, "你好呀", is_voice_source=True,
    )

    assert mgr.last_user_activity_time == 101.0
    assert mgr.last_user_message_time == 101.0
    assert mgr.last_user_engagement_time == 200.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_likely_ai_echo_voice_transcript_is_suppressed(monkeypatch):
    mgr = _make_transcript_manager()
    monkeypatch.setattr(core_module, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

    await core_module.LLMSessionManager.send_lanlan_response(mgr, "显示文本（括号也显示）")

    assert mgr._current_ai_turn_text == "显示文本（括号也显示）"
    assert mgr._recent_ai_voice_echo_text == ""
    assert mgr._recent_ai_voice_echo_at == 0.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_can_explicitly_remember_voice_echo_with_tts(monkeypatch):
    mgr = _make_manager()
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
async def test_send_lanlan_response_reports_sync_publication_time(monkeypatch):
    """The publication timestamp is sampled at the sync queue boundary."""
    mgr = _make_manager()
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
    publication_times = []

    await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "published before websocket await",
        on_published=publication_times.append,
    )

    assert publication_times == [FIXED_TS]
    queued = mgr.sync_message_queue.get_nowait()
    assert queued["data"]["text"] == "published before websocket await"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_rejects_before_stale_focus_cleanup():
    """A replaced proactive turn cannot hide the new user's thinking bubble."""
    mgr = _make_manager()
    mgr.current_speech_id = "s-user"
    mgr.last_user_engagement_time = FIXED_TS + 1.0
    mgr._push_focus_thinking = AsyncMock()

    published = await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "stale proactive",
        is_first_chunk=True,
        expected_speech_id="s-proactive",
        expected_user_engagement_time=FIXED_TS,
    )

    assert published is None
    mgr._push_focus_thinking.assert_not_awaited()
    assert mgr.sync_message_queue.empty()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_lanlan_response_guard_rechecks_after_focus_cleanup():
    """A guarded proactive bubble must not publish after engagement in its last await."""
    mgr = _make_manager()
    mgr.current_speech_id = "s-proactive"
    mgr.last_user_engagement_time = FIXED_TS

    async def engage_during_focus_cleanup(_active):
        mgr.last_user_engagement_time = FIXED_TS + 1.0

    mgr._push_focus_thinking = AsyncMock(
        side_effect=engage_during_focus_cleanup,
    )

    published = await core_module.LLMSessionManager.send_lanlan_response(
        mgr,
        "stale proactive",
        is_first_chunk=True,
        expected_speech_id="s-proactive",
        expected_user_engagement_time=FIXED_TS,
    )

    assert published is None
    assert mgr.sync_message_queue.empty()
    assert mgr._current_ai_turn_text == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mirror_assistant_speech_confirms_audio_echo_after_tts_audio(monkeypatch):
    mgr = _make_manager()
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)

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
    patch_module_clock(monkeypatch, turn_module, time=lambda: FIXED_TS)
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
    # tts_response_handler 定义在 main_logic.core.tts_runtime，读时钟的也是它。
    patch_module_clock(monkeypatch, tts_runtime_module, time=lambda: FIXED_TS)
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
    # 同上：tts_response_handler 在 main_logic.core.tts_runtime。
    patch_module_clock(monkeypatch, tts_runtime_module, time=lambda: FIXED_TS)
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
    # _clear_tts_pipeline 在 main_logic.core.tts_runtime。
    patch_module_clock(monkeypatch, tts_runtime_module, time=lambda: FIXED_TS)
    mgr.tts_thread = _FakeAliveThread()
    mgr._recent_ai_voice_echo_text = "已经播出的尾音"
    mgr._recent_ai_voice_echo_at = FIXED_TS
    mgr._pending_ai_voice_echo_text = "还没来得及播放的队列文本"
    mgr._pending_ai_voice_echo_chunks.append(("old-speech", "还没来得及播放的队列文本"))
    mgr._confirmed_ai_voice_echo_audio_speech_ids.add("old-speech")
    mgr.tts_pending_chunks = [("sid-old", "pending text")]
    completion_future = core_module.LLMSessionManager._begin_game_speech_completion_wait(
        mgr, "sid-old"
    )

    await core_module.LLMSessionManager._clear_tts_pipeline(mgr)

    assert await completion_future is False
    assert mgr._game_speech_completion_waiter is None
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_input_transcript_reports_acceptance_for_asr_bridge():
    ordinary = _make_transcript_manager()
    assert await core_module.LLMSessionManager.handle_input_transcript(
        ordinary,
        "ordinary voice input",
        is_voice_source=True,
    ) is True

    empty = _make_transcript_manager()
    assert await core_module.LLMSessionManager.handle_input_transcript(
        empty,
        "   ",
        is_voice_source=True,
    ) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_discarded_retry_drops_stream_text_from_activity_buffer():
    """A discarded reply must not stay queued for the tracker across the retry."""
    mgr = _make_manager()
    # 流式阶段每个 chunk 都走 send_lanlan_response，默认 track_ai_turn=True，
    # 所以被丢弃的那版正文此刻还躺在 buffer 里。
    mgr._current_ai_turn_text = "discarded stream body"

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        1,
        3,
        True,
    )

    assert mgr._current_ai_turn_text == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_truncated_recovery_flushes_only_recovery_body_to_tracker():
    """Turn end must see the recovery body alone, not the discarded draft too."""
    mgr = _make_manager()
    mgr._current_ai_turn_text = "discarded stream body"
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    mgr._finalize_turn_after_emit = AsyncMock()

    # fixture 的 send_lanlan_response stub 已经照真实实现按 track_ai_turn 累加；
    # recovery 路径显式传 track_ai_turn=False，改由 _track_recovery_ai_turn_text
    # 在 turn end 前一步补记。
    #
    # _flush_ai_turn_text_to_tracker 由 _emit_turn_end 调用，捕获调用当刻的 buffer。
    buffer_at_turn_end = []

    async def capture_emit(request_id):
        buffer_at_turn_end.append(mgr._current_ai_turn_text)

    mgr._emit_turn_end = capture_emit

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        3,
        3,
        False,
        '{"code":"RESPONSE_LENGTH_TRUNCATED","text":"recovered body"}',
    )

    assert buffer_at_turn_end == ["recovered body"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recovery_losing_ownership_mid_tts_leaves_no_tracker_text_for_b():
    """A's recovery body must not survive into B's tracker turn.

    Ownership can be lost inside any recovery step's await. The AI-turn text is
    therefore recorded in a synchronous step right before turn end, so an earlier
    break leaves the shared buffer untouched.
    """
    mgr = _make_manager()
    mgr.use_tts = True
    mgr.session = MagicMock()
    mgr.session._conversation_history = []
    mgr._active_text_request_id = "req-A"
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._emit_turn_end = AsyncMock()
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr._request_tts_done_for_turn = AsyncMock()

    async def feed_then_start_request_b(text, expected_speech_id=None):
        mgr._active_text_request_id = "req-B"

    mgr.feed_tts_chunk = feed_then_start_request_b

    await core_module.LLMSessionManager.handle_response_discarded(
        mgr,
        "guard",
        3,
        3,
        False,
        '{"code":"RESPONSE_LENGTH_TRUNCATED","text":"recovered body"}',
        request_id="req-A",
    )

    assert mgr._current_ai_turn_text == ""
    mgr._emit_turn_end.assert_not_awaited()



def _media_callback(summary, images):
    return {
        "event": "agent_task_callback",
        "origin": "event",
        "summary": summary,
        "detail": summary,
        "status": "completed",
        "delivery_mode": "passive",
        "coalesce_key": "",
        "media_images": list(images),
    }


def _make_callback_media_manager(offline_session):
    mgr = _make_transcript_manager()
    mgr.session = offline_session
    mgr.is_active = True
    mgr.session_ready = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr.pending_input_data = []
    mgr._starting_session_count = 0
    mgr._session_start_circuit_open = False
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._emit_cooldown_turn_end_if_needed = Mock(return_value=False)
    mgr._is_agent_enabled = Mock(return_value=False)
    mgr.agent_flags = {}
    mgr._fire_task = Mock()
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    mgr.user_language = "zh-CN"
    return mgr


def _make_offline_session_for_callback_media():
    session = object.__new__(core_module.OmniOfflineClient)
    session._pending_images = []
    session.update_max_response_length = Mock()
    session.stream_image = AsyncMock()
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_callback_media_returns_to_the_queue_when_its_turn_never_commits(
    monkeypatch,
):
    """A passive callback carrying images must survive a pre-history failure.

    The drain removes the callback and reports it delivered as soon as its text
    renders -- the deliberate best-effort contract for a plain notice. Media
    adds a boundary that contract never covered: the Offline turn still has to
    switch to its vision model, and a failure there raises before anything is
    appended to history, so text AND images vanish with no retry left.
    """
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    callback = _media_callback("agent finished", ["cb-image-b64"])
    mgr.pending_agent_callbacks = [callback]

    seen_kwargs = {}

    async def _stream_text(_text, **kwargs):
        seen_kwargs.update(kwargs)
        raise RuntimeError("switch_model failed: bad credential")

    session.stream_text = AsyncMock(side_effect=_stream_text)
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message", lambda _n, _t: None
    )

    # 外层 handler 会把 provider 异常吞成日志，这里不该期待它冒出来。
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "什么情况"},
    )

    # 这一轮确实是带图的（否则本用例根本没测到回滚路径）。
    assert seen_kwargs.get("system_prefix_images") == ["cb-image-b64"]
    assert mgr.pending_agent_callbacks == [callback]
    assert callback["media_images"] == ["cb-image-b64"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_callback_media_is_not_requeued_once_its_turn_reached_history(
    monkeypatch,
):
    """After the turn commits, the content is in front of the model.

    Rolling back on a later failure would deliver the same callback twice.
    """
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    callback = _media_callback("agent finished", ["cb-image-b64"])
    mgr.pending_agent_callbacks = [callback]

    async def _stream_text(_text, **kwargs):
        kwargs["on_turn_committed"]()
        raise RuntimeError("provider dropped mid-stream")

    session.stream_text = AsyncMock(side_effect=_stream_text)
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message", lambda _n, _t: None
    )

    # 外层 handler 会把 provider 异常吞成日志，这里不该期待它冒出来。
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "什么情况"},
    )

    assert mgr.pending_agent_callbacks == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_typed_text_cancels_the_in_flight_offline_stream_first(monkeypatch):
    """Typed text must stop the producer, not just rotate the speech id.

    An Offline session can be mid-stream on an independent-ASR external turn
    (or an earlier text response). Both turns share ``_is_responding`` and both
    append to the same history, and rotating ``current_speech_id`` without
    cancelling leaves the old stream emitting deltas under the new id.
    """
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    order = []
    sid_at_interrupt = {}

    async def _handle_interruption():
        order.append("interrupt")
        sid_at_interrupt["sid"] = mgr.current_speech_id

    async def _stream_text(_text, **_kwargs):
        order.append("stream_text")

    session.handle_interruption = AsyncMock(side_effect=_handle_interruption)
    session.stream_text = AsyncMock(side_effect=_stream_text)
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message", lambda _n, _t: None
    )
    old_sid = mgr.current_speech_id

    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "text", "data": "别说了，换个话题"},
    )

    assert order == ["interrupt", "stream_text"]
    # 取消要发生在 speech_id 轮换**之前**，否则旧流的 delta 会挂到新 id 上。
    assert sid_at_interrupt["sid"] == old_sid
    assert mgr.current_speech_id != old_sid


@pytest.mark.unit
@pytest.mark.asyncio
async def test_callback_media_returns_when_cancelled_before_the_stream_begins(
    monkeypatch,
):
    """The rollback window is the whole post-drain stretch, not just stream_text.

    The callback leaves the queue and is reported delivered the moment its text
    renders, but several awaits still stand between that and the turn reaching
    history -- the Focus decision and the thinking-bubble pulse. A teardown that
    cancels this input task in there loses the callback's text AND its images
    with no retry left.
    """
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    callback = _media_callback("agent finished", ["cb-image-1"])
    mgr.pending_agent_callbacks = [callback]
    session.stream_text = AsyncMock()

    async def cancelled_focus_decision(_text):
        raise asyncio.CancelledError()

    mgr._focus_inline_decision = cancelled_focus_decision
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message", lambda _n, _t: None
    )

    # 取消必须继续向上传播（回滚是顺手做的，不是把取消吃掉）。
    with pytest.raises(asyncio.CancelledError):
        await core_module.LLMSessionManager._process_stream_data_internal(
            mgr,
            {"input_type": "text", "data": "什么情况"},
        )

    # 这一轮从没到达 stream_text，callback 必须完好回队。
    session.stream_text.assert_not_awaited()
    assert mgr.pending_agent_callbacks == [callback]
    assert callback["media_images"] == ["cb-image-1"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_focus_pulse_is_cleared_when_its_own_send_is_cancelled(monkeypatch):
    """The pulse that turns the bubble ON must sit inside the scope that turns it OFF.

    ``_push_focus_thinking(True)`` itself awaits the websocket lock and
    ``send_json``. A teardown that cancels the turn right there has already set
    the active flag and queued the notification, so leaving it outside the
    cleanup scope strands the thinking bubble until some later turn happens to
    clear it.
    """
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr.pending_agent_callbacks = []
    session.stream_text = AsyncMock()
    mgr._focus_inline_decision = AsyncMock(return_value=True)
    pulses = []

    async def push_focus_thinking(active):
        pulses.append(active)
        if active:
            # 拆除正好卡在这次投递上。
            raise asyncio.CancelledError()

    mgr._push_focus_thinking = push_focus_thinking
    monkeypatch.setattr(
        core_module, "dispatch_text_user_message", lambda _n, _t: None
    )

    with pytest.raises(asyncio.CancelledError):
        await core_module.LLMSessionManager._process_stream_data_internal(
            mgr,
            {"input_type": "text", "data": "凝神这一句"},
        )

    session.stream_text.assert_not_awaited()
    # 关键：亮起之后必须有一次熄灭，否则气泡一直卡着。
    assert pulses == [True, False]


@pytest.mark.unit
def test_game_speech_preload_glues_spaces_only_for_chinese_text():
    """The preload path must normalize spaces the way the speak path does.

    ``replace_blank`` drops every ASCII space whose neighbour is non-ASCII, so
    running it unconditionally glued Korean/Cyrillic/Thai words together --
    scripts that separate words WITH those spaces. ``normalize_text``, which is
    what the real speak path runs, gates it on ``contains_chinese``; preload has
    to match or the cached audio differs from the spoken line.
    """

    normalize = core_module.LLMSessionManager._normalize_game_speech_preload_text

    # Scripts that are non-ASCII but space-delimited keep their word boundaries.
    assert normalize("안녕하세요 여러분", normalize_spaces=True) == (
        "안녕하세요 여러분"
    )
    assert normalize("Привет мир", normalize_spaces=True) == (
        "Привет мир"
    )

    # The artifact this normalization exists for is still removed, so the guard
    # above cannot be satisfied by simply dropping the normalization.
    assert normalize("你 好 世 界", normalize_spaces=True) == "你好世界"

    # And the ws_bistream providers still opt out of it entirely.
    assert normalize("你 好", normalize_spaces=False) == "你 好"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_speech_sends_never_split_a_frame():
    """One audio frame is a header plus its payload, and it must stay atomic.

    A mini-game line replayed from the audio cache is written from the HTTP task
    while ordinary TTS is still streaming from the response handler
    (``interruptExisting`` defaults to false). ``send_speech`` writes the JSON
    header and the binary payload in two separate awaits, and the frontend pairs
    them FIFO -- so an interleave of header A, header B, bytes A makes the
    browser play A's bytes under B's speech id, gain and correlation.
    """

    sent: list[tuple[str, str]] = []

    class _ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class _InterleavingWebsocket:
        client_state = _ConnectedState()

        async def send_json(self, payload):
            sent.append(("header", str(payload.get("speech_id") or "")))
            # The yield the defect needs: without a lock the sibling task runs
            # here, between a header and its payload.
            await asyncio.sleep(0)

        async def send_bytes(self, data):
            sent.append(("payload", data.decode()))
            await asyncio.sleep(0)

    mgr = _make_manager()
    mgr.websocket = _InterleavingWebsocket()
    mgr._game_speech_correlation_for = lambda _speech_id: ""
    mgr.speech_playback_gain = lambda _speech_id: 1.0
    mgr._speech_output_total = 0
    mgr._last_speech_output_time = 0.0
    mgr._last_speech_output_bytes = 0

    await asyncio.gather(
        core_module.LLMSessionManager.send_speech(mgr, b"a", speech_id="a"),
        core_module.LLMSessionManager.send_speech(mgr, b"b", speech_id="b"),
    )

    assert len(sent) == 4, sent
    for index in range(0, len(sent), 2):
        kind, header_id = sent[index]
        assert kind == "header", sent
        payload_kind, payload_body = sent[index + 1]
        assert payload_kind == "payload", sent
        assert payload_body == header_id, (
            f"audio payload {payload_body!r} was written under header {header_id!r}: "
            "concurrent sends split a frame"
        )
    # Both frames really did go out, so the assertion above cannot be satisfied
    # by a send that silently dropped one of them.
    assert {entry[1] for entry in sent} == {"a", "b"}, sent


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_reconnect_mid_frame_cannot_split_it_across_two_sockets():
    """The frame lock keeps other SENDERS out; it does not pin ``self.websocket``.

    Reconnect and teardown reassign that attribute and never take the lock, so
    re-reading it per await could put an ``audio_chunk`` header on the retired
    socket and its payload on the replacement -- the same corruption the lock
    exists to prevent, arriving through the other door.
    """

    class _ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class _RecordingWebsocket:
        client_state = _ConnectedState()

        def __init__(self, name, on_send_json=None):
            self.name = name
            self.received: list[tuple[str, str]] = []
            self._on_send_json = on_send_json

        async def send_json(self, payload):
            self.received.append(("header", str(payload.get("speech_id") or "")))
            if self._on_send_json is not None:
                self._on_send_json()

        async def send_bytes(self, data):
            self.received.append(("payload", data.decode()))

    mgr = _make_manager()
    replacement = _RecordingWebsocket("replacement")
    # The reconnect lands exactly between the header and its payload.
    retired = _RecordingWebsocket(
        "retired", on_send_json=lambda: setattr(mgr, "websocket", replacement),
    )
    mgr.websocket = retired
    mgr._game_speech_correlation_for = lambda _speech_id: ""
    mgr.speech_playback_gain = lambda _speech_id: 1.0
    mgr._speech_output_total = 0
    mgr._last_speech_output_time = 0.0
    mgr._last_speech_output_bytes = 0

    assert await core_module.LLMSessionManager.send_speech(mgr, b"x", speech_id="x")

    assert retired.received == [("header", "x"), ("payload", "x")], retired.received
    assert replacement.received == [], (
        f"a reconnect mid-frame moved part of the frame onto the new socket: "
        f"{replacement.received}"
    )
    # The reconnect really did happen, so the assertion above cannot be
    # satisfied by a probe whose swap never fired.
    assert mgr.websocket is replacement


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cached_replay_is_not_interleaved_by_a_concurrent_stream():
    """A cached utterance goes out as one run, terminal signal included.

    A per-frame lock keeps each header with its own payload but releases between
    frames, so a cached replay overlapping ordinary TTS still arrives as A1, B1,
    A2, B2 -- and the frontend schedules decoded chunks in arrival order, so the
    two lines are heard interwoven.
    """

    labels: list[str] = []

    class _ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class _YieldingWebsocket:
        client_state = _ConnectedState()

        async def send_json(self, payload):
            labels.append(str(payload.get("speech_id") or ""))
            # The yield the defect needs, at every frame boundary.
            await asyncio.sleep(0)

        async def send_bytes(self, data):
            labels.append("cached" if data.startswith(b"c") else "live")
            await asyncio.sleep(0)

    mgr = _make_manager()
    mgr.websocket = _YieldingWebsocket()
    mgr._game_speech_correlation_for = lambda _speech_id: ""
    mgr.speech_playback_gain = lambda _speech_id: 1.0
    mgr.release_speech_playback_gain = lambda _speech_id: None
    mgr._clear_game_speech_correlation = lambda _speech_id: None
    mgr._speech_output_total = 0
    mgr._last_speech_output_time = 0.0
    mgr._last_speech_output_bytes = 0

    await asyncio.gather(
        core_module.LLMSessionManager.send_cached_speech_batch(
            mgr, [b"c1", b"c2"], "cached",
        ),
        core_module.LLMSessionManager.send_speech(mgr, b"live", speech_id="live"),
    )

    # 2 headers + 2 payloads + the audio_done for the batch, 1 header + 1 payload
    # for the streaming frame.
    assert len(labels) == 7, labels
    cached_positions = [index for index, label in enumerate(labels) if label == "cached"]
    assert len(cached_positions) == 5, labels
    assert cached_positions == list(
        range(cached_positions[0], cached_positions[0] + 5)
    ), f"the cached utterance was split by the concurrent stream: {labels}"
    # The concurrent frame really did go out, so the assertion above cannot be
    # satisfied by a probe where nothing competed with the batch.
    assert labels.count("live") == 2, labels


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_reconnect_while_queued_for_the_frame_lock_drops_the_frame():
    """Pinning keeps a frame whole; it cannot make a retired socket the right one.

    The existing sibling test covers a swap that lands AFTER ``send_json`` has
    already started. This one covers the other window: a call that pinned the
    live socket, then waited for the frame lock while reconnect replaced it.
    Writing there delivers to nobody and would hide the loss from the caller.
    """

    class _ConnectedState:
        CONNECTED = "connected"

        def __eq__(self, other):
            return other == self.CONNECTED

    class _RecordingWebsocket:
        client_state = _ConnectedState()

        def __init__(self, name, before_first_send=None):
            self.name = name
            self.received: list[str] = []
            self._before_first_send = before_first_send

        async def send_json(self, payload):
            hook, self._before_first_send = self._before_first_send, None
            if hook is not None:
                # Yield BEFORE swapping so the queued caller has already pinned
                # this socket; the swap then happens while it waits for the lock.
                await asyncio.sleep(0)
                hook()
            self.received.append(f"header:{payload.get('speech_id')}")

        async def send_bytes(self, data):
            self.received.append(f"payload:{data.decode()}")

    mgr = _make_manager()
    replacement = _RecordingWebsocket("replacement")
    live = _RecordingWebsocket(
        "live", before_first_send=lambda: setattr(mgr, "websocket", replacement),
    )
    mgr.websocket = live
    mgr._game_speech_correlation_for = lambda _speech_id: ""
    mgr.speech_playback_gain = lambda _speech_id: 1.0
    mgr._speech_output_total = 0
    mgr._last_speech_output_time = 0.0
    mgr._last_speech_output_bytes = 0

    holder, queued = await asyncio.gather(
        core_module.LLMSessionManager.send_speech(mgr, b"first", speech_id="first"),
        core_module.LLMSessionManager.send_speech(mgr, b"second", speech_id="second"),
    )

    assert holder is True
    assert queued is False, "a frame was written to a socket that had been replaced"
    assert live.received == ["header:first", "payload:first"], live.received
    assert replacement.received == [], replacement.received
    # The swap really happened and the second call really did pin the old socket,
    # so the assertion above cannot pass by the probe never racing at all.
    assert mgr.websocket is replacement

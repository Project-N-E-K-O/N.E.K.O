import asyncio
import os
import struct
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.core import asr_runtime as core_asr_runtime_module
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.core.asr_runtime import (
    _AudioDurationQueue as AudioDurationQueue,
    _HotSwapAudioBuffer as HotSwapAudioBuffer,
    _HotSwapAudioFrame as HotSwapAudioFrame,
    _QueuedMicFrame as QueuedMicFrame,
)
from main_logic.asr_client.lifecycle import (
    VoiceIngressToken,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy


async def test_starting_session_audio_does_not_enter_pending_input_data():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr.pending_input_data = []
    mgr.input_cache_lock = asyncio.Lock()

    await LLMSessionManager._stream_data_now(
        mgr, {"input_type": "audio", "data": [0] * 480}
    )

    assert mgr.pending_input_data == []


async def test_goodbye_silent_drops_live_vision_stream_before_processing():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.goodbye_silent = True
    mgr._stream_data_now = AsyncMock()

    await LLMSessionManager.stream_data(
        mgr,
        {"input_type": "screen", "data": "data:image/jpeg;base64,abc"},
    )

    mgr._stream_data_now.assert_not_awaited()


async def test_live_vision_stream_does_not_auto_start_session_when_inactive():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.goodbye_silent = False
    mgr.session_ready = False
    mgr._starting_session_count = 0
    mgr.session = None
    mgr.is_active = False
    mgr.input_cache_lock = asyncio.Lock()
    mgr.start_session = AsyncMock()

    await LLMSessionManager._stream_data_now(
        mgr,
        {"input_type": "screen", "data": "data:image/jpeg;base64,abc"},
    )

    mgr.start_session.assert_not_awaited()


async def test_goodbye_silent_drops_live_vision_stream_in_internal_processor():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.goodbye_silent = True
    mgr.session = MagicMock()
    mgr.session.stream_image = AsyncMock()
    mgr.is_active = True

    await LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "camera", "data": "data:image/jpeg;base64,abc"},
    )

    mgr.session.stream_image.assert_not_called()


async def test_flush_pending_input_data_routes_audio_through_bounded_queue():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    audio_msg = {"input_type": "audio", "data": [1] * 480}
    text_msg = {"input_type": "text", "data": "hello"}
    mgr.pending_input_data = [audio_msg, text_msg]
    mgr.input_cache_lock = asyncio.Lock()
    mgr.session = object()
    mgr.is_active = True
    mgr._enqueue_audio_stream_data = AsyncMock()
    mgr._process_stream_data_internal = AsyncMock()

    await LLMSessionManager._flush_pending_input_data(mgr)

    mgr._enqueue_audio_stream_data.assert_awaited_once_with(audio_msg)
    mgr._process_stream_data_internal.assert_awaited_once_with(text_msg)
    assert mgr.pending_input_data == []


def _queue_token() -> VoiceIngressToken:
    return VoiceIngressToken(1, "socket", 1, 1, 1)


def _hot_swap_frame(
    token: VoiceIngressToken,
    *,
    samples: int = 160,
    speech_probability: float | None = 0.5,
    rnnoise_available: bool = True,
) -> HotSwapAudioFrame:
    return HotSwapAudioFrame(
        pcm16=b"\x01\x00" * samples,
        token=token,
        speech_probability=speech_probability,
        rnnoise_available=rnnoise_available,
    )


def _authorize_core_lease(mgr: LLMSessionManager) -> None:
    mgr._voice_lease_synchronized = True
    mgr._voice_lease_owner = "core"
    mgr._voice_input_suppressed = False


async def test_native_audio_without_asr_lifecycle_reaches_internal_processor():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._set_microphone_route("native")
    mgr._ensure_audio_stream_worker = lambda: None
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=10,
    )
    mgr._audio_stream_dropped_total = 0
    mgr._last_audio_stream_backlog_log_time = 0.0
    message = {"input_type": "audio", "data": [1] * 160}

    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)

    frame = await mgr._audio_stream_queue.get()
    assert frame.message is message
    assert mgr._ingress_token_matches(frame.token)
    mgr._set_microphone_route("blocked")
    assert not mgr._ingress_token_matches(frame.token)
    mgr._audio_stream_queue.task_done()


def test_audio_stream_queue_uses_ceiling_duration_accounting():
    frame = QueuedMicFrame.from_message(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 161,
        },
        token=_queue_token(),
        received_at=1.0,
    )

    assert frame.duration_us == 10_063


async def test_audio_stream_queue_clears_whole_candidate_when_full():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._asr_runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    mgr._asr_runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=2,
    )
    mgr._audio_stream_dropped_total = 0
    mgr._last_audio_stream_backlog_log_time = 0.0
    mgr._ensure_audio_stream_worker = lambda: None
    mgr._bg_tasks = set()
    mgr._asr_runtime.abort = AsyncMock()

    def message(seq: int) -> dict:
        return {
            "seq": seq,
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [seq] * 160,
        }

    await LLMSessionManager._enqueue_audio_stream_data(mgr, message(1))
    await LLMSessionManager._enqueue_audio_stream_data(mgr, message(2))
    await LLMSessionManager._enqueue_audio_stream_data(mgr, message(3))
    await asyncio.gather(*list(mgr._bg_tasks))

    assert mgr._audio_stream_dropped_total == 3
    assert mgr._audio_stream_queue.empty()
    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")


async def test_active_audio_queue_overflow_aborts_turn_then_resumes_local_listen():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.send_status = AsyncMock()
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._asr_runtime._asr_provider = "qwen"
    mgr._asr_runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    mgr._asr_runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    mgr._asr_runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    mgr._asr_runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    mgr._asr_runtime._asr_current_ingress_token = mgr._capture_ingress_token()
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=1,
    )
    mgr._audio_stream_dropped_total = 0
    mgr._last_audio_stream_backlog_log_time = 0.0
    mgr._ensure_audio_stream_worker = lambda: None
    mgr._bg_tasks = set()
    message = {
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [1] * 160,
    }

    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)
    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)
    await asyncio.gather(*list(mgr._bg_tasks))

    assert mgr._asr_runtime._asr_lifecycle is not None
    assert (
        mgr._asr_runtime._asr_lifecycle.snapshot.state
        is VoiceLifecycleState.LOCAL_LISTEN
    )
    assert mgr._audio_stream_queue.empty()
    assert any(
        "ASR_INGRESS_BACKPRESSURE" in call.args[0]
        for call in mgr.send_status.await_args_list
    )
    assert mgr._omni_mic_audio_bytes == 0


async def test_audio_worker_leaves_runtime_generation_validation_to_submit():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._asr_runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    mgr._asr_runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=4,
    )
    mgr._audio_stream_dropped_total = 0
    mgr._process_microphone_stream_data = AsyncMock()
    frame = QueuedMicFrame.from_message(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        token=mgr._capture_ingress_token(),
    )
    mgr._audio_stream_queue.put_nowait(frame)
    mgr._asr_runtime._asr_audio_generation += 1

    worker = asyncio.create_task(LLMSessionManager._audio_stream_worker_loop(mgr))
    while not mgr._audio_stream_queue.empty():
        await asyncio.sleep(0)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    mgr._process_microphone_stream_data.assert_awaited_once_with(
        frame.message,
        ingress_token=frame.token,
        audio_stream_epoch=frame.audio_stream_epoch,
        ingress_sequence=frame.ingress_sequence,
    )
    assert mgr._audio_stream_dropped_total == 0


async def test_audio_worker_does_not_wait_for_core_session_readiness():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._asr_runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    mgr._asr_runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=4,
    )
    mgr._audio_stream_dropped_total = 0
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr._process_microphone_stream_data = AsyncMock()
    frame = QueuedMicFrame.from_message(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        token=mgr._capture_ingress_token(),
    )
    mgr._audio_stream_queue.put_nowait(frame)

    worker = asyncio.create_task(LLMSessionManager._audio_stream_worker_loop(mgr))
    while not mgr._audio_stream_queue.empty():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    mgr._process_microphone_stream_data.assert_awaited_once_with(
        frame.message,
        ingress_token=frame.token,
        audio_stream_epoch=frame.audio_stream_epoch,
        ingress_sequence=frame.ingress_sequence,
    )


async def test_inflight_audio_is_dropped_when_epoch_changes():
    mgr = _make_routable_audio_manager(True)

    async def advance_epoch(*_args, **_kwargs):
        mgr._audio_stream_epoch += 1
        return ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.5, True)

    mgr._voice_input_audio_pipeline.process = AsyncMock(side_effect=advance_epoch)

    await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._route_microphone_audio.assert_not_awaited()
    mgr.session.stream_audio.assert_not_awaited()


def _make_routable_audio_manager(route_result: bool):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._set_microphone_route("native")
    mgr.session_ready = True
    mgr._starting_session_count = 0
    mgr.is_active = True
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._session_start_circuit_open = False
    mgr._audio_stream_epoch = 0
    mgr.session_closed_by_server = False
    mgr.last_audio_send_error_time = 0.0
    mgr.audio_error_log_interval = 2.0
    mgr.is_hot_swap_imminent = False
    mgr.is_flushing_hot_swap_cache = False
    mgr.hot_swap_cache_lock = asyncio.Lock()
    mgr._route_microphone_audio = AsyncMock(return_value=route_result)
    mgr._record_omni_microphone_audio = MagicMock()
    mgr._voice_input_audio_pipeline.process = AsyncMock(
        return_value=ProcessedVoiceFrame(
            pcm16=b"\x01\x00" * 160,
            sample_rate_hz=16_000,
            speech_probability=0.5,
            rnnoise_available=True,
        )
    )

    class _RealtimeSession(OmniRealtimeClient):
        def __init__(self):
            self.ws = object()
            self._fatal_error_occurred = False
            self._audio_processor = object()
            self.stream_audio = AsyncMock()

        async def process_audio_chunk_async(self, audio_bytes):
            return audio_bytes

    mgr.session = _RealtimeSession()
    return mgr


async def _process_microphone_message(
    mgr: LLMSessionManager,
    message: dict,
) -> VoiceIngressToken:
    token = mgr._capture_ingress_token()
    await LLMSessionManager._process_microphone_stream_data(
        mgr,
        message,
        ingress_token=token,
    )
    return token


async def test_independent_asr_route_does_not_send_microphone_audio_to_omni():
    mgr = _make_routable_audio_manager(True)

    await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._route_microphone_audio.assert_awaited_once()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_binary_audio_sample_rate_contract_reaches_audio_pipeline():
    mgr = _make_routable_audio_manager(True)

    await _process_microphone_message(
        mgr,
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 480,
        },
    )

    mgr._voice_input_audio_pipeline.process.assert_awaited_once_with(
        struct.pack("<480h", *([1] * 480)),
        sample_rate_hz=16_000,
    )


async def test_blocked_route_never_sends_microphone_audio_to_omni():
    mgr = _make_routable_audio_manager(True)

    await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._route_microphone_audio.assert_awaited_once()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_independent_audio_route_precedes_omni_websocket_checks():
    mgr = _make_routable_audio_manager(True)
    mgr.session.ws = None
    mgr._voice_input_audio_pipeline.process = AsyncMock(
        return_value=ProcessedVoiceFrame(
            pcm16=b"\x02\x00" * 160,
            sample_rate_hz=16_000,
            speech_probability=0.8,
            rnnoise_available=True,
        )
    )

    token = await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._voice_input_audio_pipeline.process.assert_awaited_once()
    mgr._route_microphone_audio.assert_awaited_once_with(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.8,
        rnnoise_available=True,
        ingress_token=token,
    )
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_fatal_omni_state_does_not_block_independent_asr_audio():
    mgr = _make_routable_audio_manager(True)
    mgr.session._fatal_error_occurred = True

    await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._voice_input_audio_pipeline.process.assert_awaited_once()
    mgr._route_microphone_audio.assert_awaited_once()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_independent_audio_route_does_not_require_omni_session_container():
    mgr = _make_routable_audio_manager(True)
    mgr.session = type("TextOnlyCore", (), {})()
    mgr.start_session = AsyncMock()
    mgr.end_session = AsyncMock()

    token = await _process_microphone_message(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    mgr._route_microphone_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.5,
        rnnoise_available=True,
        ingress_token=token,
    )
    mgr.start_session.assert_not_awaited()
    mgr.end_session.assert_not_awaited()


async def test_active_teardown_blocks_audio_while_independent_asr_close_waits():
    mgr = _make_routable_audio_manager(True)
    del mgr._route_microphone_audio
    mgr._init_asr_runtime_state()
    mgr._set_microphone_route("independent")
    mgr._asr_runtime._asr_provider = "dummy"
    mgr.lock = asyncio.Lock()
    mgr._user_session_abandon_epoch = 0
    mgr._reset_tts_retry_state = lambda: None
    mgr._reset_proactive_gate = lambda: None

    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class _WaitingAsr:
        async def close(self):
            close_started.set()
            await allow_close.wait()

    mgr._asr_runtime._asr_session = _WaitingAsr()

    end_task = asyncio.create_task(LLMSessionManager.end_session(mgr))
    await close_started.wait()
    try:
        assert mgr.is_active is True
        assert mgr.session_ready is True
        assert mgr._asr_route_mode == "blocked"

        await LLMSessionManager._process_stream_data_internal(
            mgr,
            {"input_type": "audio", "data": [1] * 480},
        )

        mgr.session.stream_audio.assert_not_awaited()
        mgr._record_omni_microphone_audio.assert_not_called()
    finally:
        end_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await end_task

    assert mgr._asr_route_mode == "blocked"


async def test_hot_swap_flush_preserves_identity_and_detector_metadata():
    mgr = _make_routable_audio_manager(True)
    token = _queue_token()
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(
        _hot_swap_frame(
            token,
            speech_probability=0.75,
            rnnoise_available=True,
        )
    )

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._ingress_token_matches.assert_called_once_with(token)
    mgr._route_microphone_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.75,
        rnnoise_available=True,
        ingress_token=token,
    )
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()
    assert not mgr.hot_swap_audio_cache


async def test_hot_swap_flush_supports_text_only_core_without_omni_pcm():
    mgr = _make_routable_audio_manager(False)
    omni_session = mgr.session
    mgr.session = type("TextOnlyCore", (), {})()
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(_queue_token()))

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._route_microphone_audio.assert_awaited_once()
    omni_session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_hot_swap_flush_discards_stale_generation():
    mgr = _make_routable_audio_manager(True)
    mgr._ingress_token_matches = MagicMock(return_value=False)
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(_queue_token()))

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._route_microphone_audio.assert_not_awaited()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_hot_swap_rebinds_queued_raw_and_processed_cache_in_order():
    mgr = _make_routable_audio_manager(True)
    old_token = mgr._capture_ingress_token()
    mgr.is_hot_swap_imminent = True
    mgr._set_microphone_route("blocked")

    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(
        HotSwapAudioFrame(pcm16=b"\x10\x00" * 160, token=old_token)
    )
    mgr._voice_input_audio_pipeline.process = AsyncMock(
        return_value=ProcessedVoiceFrame(
            b"\x20\x00" * 160,
            16_000,
            0.8,
            True,
        )
    )
    message = {
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [2] * 160,
    }
    mgr._audio_stream_queue.put_nowait(
        QueuedMicFrame.from_message(message, token=old_token)
    )
    worker = asyncio.create_task(LLMSessionManager._audio_stream_worker_loop(mgr))
    try:
        deadline = asyncio.get_running_loop().time() + 1
        while len(mgr.hot_swap_audio_cache) < 2:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("queued raw frame was not processed")
            await asyncio.sleep(0)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    assert mgr._audio_stream_dropped_total == 0
    mgr._set_microphone_route("independent")
    current_token = mgr._capture_ingress_token()
    assert current_token.route_generation != old_token.route_generation

    routed: list[tuple[bytes, VoiceIngressToken]] = []

    async def route(
        pcm16: bytes,
        *,
        ingress_token: VoiceIngressToken,
        **_kwargs,
    ) -> bool:
        routed.append((pcm16[:2], ingress_token))
        return True

    mgr._route_microphone_audio = AsyncMock(side_effect=route)
    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    assert routed == [
        (b"\x10\x00", current_token),
        (b"\x20\x00", current_token),
    ]
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_hot_swap_rebinds_inflight_pipeline_result_after_core_swap():
    mgr = _make_routable_audio_manager(True)
    old_token = mgr._capture_ingress_token()
    old_pipeline = mgr._voice_input_audio_pipeline
    processing_started = asyncio.Event()
    release_processing = asyncio.Event()

    async def process(*_args, **_kwargs):
        processing_started.set()
        await release_processing.wait()
        return ProcessedVoiceFrame(b"\x30\x00" * 160, 16_000, 0.9, True)

    old_pipeline.process = AsyncMock(side_effect=process)
    processing = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [3] * 160},
            ingress_token=old_token,
        )
    )
    await asyncio.wait_for(processing_started.wait(), 1)

    mgr.is_hot_swap_imminent = True
    mgr._set_microphone_route("blocked")
    mgr.session = type("NewCoreSession", (), {"stream_audio": AsyncMock()})()
    mgr._voice_input_audio_pipeline = type(
        "NewPipeline",
        (),
        {"process": AsyncMock(), "close": AsyncMock()},
    )()
    mgr._set_microphone_route("independent")
    current_token = mgr._capture_ingress_token()
    routed: list[tuple[bytes, VoiceIngressToken]] = []

    async def route(
        pcm16: bytes,
        *,
        ingress_token: VoiceIngressToken,
        **_kwargs,
    ) -> bool:
        routed.append((pcm16, ingress_token))
        return True

    mgr._route_microphone_audio = AsyncMock(side_effect=route)
    flush = asyncio.create_task(LLMSessionManager._flush_hot_swap_audio_cache(mgr))
    await asyncio.sleep(0)
    assert not flush.done()

    release_processing.set()
    await asyncio.wait_for(asyncio.gather(processing, flush), 1)

    assert routed == [(b"\x30\x00" * 160, current_token)]
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_hot_swap_queue_full_retry_rebinds_without_silent_drop():
    mgr = _make_routable_audio_manager(True)
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=20_000,
        max_frames=1,
    )
    mgr._audio_stream_worker_task = asyncio.current_task()
    old_token = mgr._capture_ingress_token()
    first_message = {
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [1] * 160,
    }
    second_message = {
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [2] * 160,
    }
    mgr._audio_stream_queue.put_nowait(
        QueuedMicFrame.from_message(first_message, token=old_token)
    )

    async def enter_hot_swap_and_free_slot() -> None:
        mgr.is_hot_swap_imminent = True
        mgr._set_microphone_route("blocked")
        mgr._audio_stream_queue.get_nowait()
        mgr._audio_stream_queue.task_done()

    transition = asyncio.create_task(enter_hot_swap_and_free_slot())
    await LLMSessionManager._enqueue_audio_stream_data(mgr, second_message)
    await transition

    rebound = mgr._audio_stream_queue.get_nowait()
    mgr._audio_stream_queue.task_done()
    assert rebound.message == second_message
    assert rebound.token == mgr._capture_ingress_token()
    assert mgr._audio_stream_dropped_total == 0


@pytest.mark.parametrize(
    "stale_reason",
    [
        "session_epoch",
        "audio_generation",
        "audio_stream_epoch",
        "connection",
        "lease",
        "hard_mute",
        "focus_suppressed",
        "game",
    ],
)
def test_hot_swap_never_rebinds_lease_mute_or_game_identity(
    stale_reason: str,
):
    mgr = _make_routable_audio_manager(True)
    old_token = mgr._capture_ingress_token()
    old_audio_stream_epoch = mgr._audio_stream_epoch
    mgr.is_hot_swap_imminent = True
    mgr._set_microphone_route("blocked")
    mgr._set_microphone_route("independent")

    if stale_reason == "session_epoch":
        mgr._asr_runtime._asr_session_epoch += 1
    elif stale_reason == "audio_generation":
        mgr._asr_runtime._asr_audio_generation += 1
    elif stale_reason == "audio_stream_epoch":
        mgr._audio_stream_epoch += 1
    elif stale_reason == "connection":
        mgr._voice_lease_connection_id = "replacement"
    elif stale_reason == "lease":
        mgr._voice_lease_generation += 1
    elif stale_reason == "hard_mute":
        mgr._voice_lease_hard_muted = True
    elif stale_reason == "focus_suppressed":
        mgr._voice_lease_focus_suppressed = True
    else:
        mgr._voice_lease_owner = "game"
        mgr._voice_input_consumer_bindings["game"] = object()

    assert (
        mgr._rebind_hot_swap_ingress_token(
            old_token,
            audio_stream_epoch=old_audio_stream_epoch,
        )
        is None
    )


def test_hot_swap_rebind_changes_only_route_generation():
    mgr = _make_routable_audio_manager(True)
    old_token = mgr._capture_ingress_token()
    audio_stream_epoch = mgr._audio_stream_epoch
    mgr.is_hot_swap_imminent = True
    mgr._set_microphone_route("blocked")

    rebound = mgr._rebind_hot_swap_ingress_token(
        old_token,
        audio_stream_epoch=audio_stream_epoch,
    )

    assert rebound == mgr._capture_ingress_token()
    assert rebound is not None
    assert rebound.session_epoch == old_token.session_epoch
    assert rebound.audio_generation == old_token.audio_generation
    assert rebound.connection_id == old_token.connection_id
    assert rebound.lease_generation == old_token.lease_generation
    assert rebound.route_generation != old_token.route_generation


async def test_hot_swap_flush_rechecks_cutoff_after_event_edge():
    mgr = _make_routable_audio_manager(True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.is_hot_swap_imminent = True
    token = mgr._capture_ingress_token()
    started = [asyncio.Event() for _ in range(3)]
    release = [asyncio.Event() for _ in range(3)]
    call_index = 0

    async def process(*_args, **_kwargs):
        nonlocal call_index
        index = call_index
        call_index += 1
        started[index].set()
        await release[index].wait()
        return ProcessedVoiceFrame(
            bytes([index + 1, 0]) * 160,
            16_000,
            0.8,
            True,
        )

    mgr._voice_input_audio_pipeline.process = AsyncMock(side_effect=process)
    first = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [1] * 160},
            ingress_token=token,
        )
    )
    second = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [2] * 160},
            ingress_token=token,
        )
    )
    await asyncio.wait_for(
        asyncio.gather(started[0].wait(), started[1].wait()),
        1,
    )
    flush = asyncio.create_task(LLMSessionManager._flush_hot_swap_audio_cache(mgr))
    await asyncio.sleep(0)

    release[0].set()
    await first
    third = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [3] * 160},
            ingress_token=token,
        )
    )
    await asyncio.wait_for(started[2].wait(), 1)
    assert not flush.done()

    release[1].set()
    await second
    await asyncio.wait_for(flush, 1)
    assert not third.done()
    assert not mgr.is_hot_swap_imminent
    assert not mgr.is_flushing_hot_swap_cache

    release[2].set()
    await asyncio.wait_for(third, 1)
    # The native replay coalesces the two cached frames into one send; the
    # third frame is routed live after the flush completes.
    assert mgr._route_microphone_audio.await_count == 2
    mgr._asr_runtime.abort.assert_not_awaited()


async def test_hot_swap_overflow_blocks_whole_candidate():
    mgr = _make_routable_audio_manager(True)
    token = _queue_token()
    mgr.is_hot_swap_imminent = True
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=10)
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(token))

    await LLMSessionManager._process_microphone_stream_data(
        mgr,
        {"input_type": "audio", "sample_rate_hz": 16_000, "data": [1] * 160},
        ingress_token=token,
    )

    assert not mgr.hot_swap_audio_cache
    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")
    mgr._route_microphone_audio.assert_not_awaited()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()


async def test_hot_swap_flush_orders_inflight_live_then_cached_audio():
    mgr = _make_routable_audio_manager(True)
    token = _queue_token()
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(
        HotSwapAudioFrame(pcm16=b"\x10\x00" * 160, token=token)
    )
    mgr._voice_input_audio_pipeline.process = AsyncMock(
        side_effect=[
            ProcessedVoiceFrame(b"\x20\x00" * 160, 16_000, 0.8, True),
            ProcessedVoiceFrame(b"\x30\x00" * 160, 16_000, 0.8, True),
        ]
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    routed: list[bytes] = []

    async def route(pcm16: bytes, **_kwargs) -> bool:
        routed.append(pcm16)
        if pcm16.startswith(b"\x20\x00"):
            first_started.set()
            await release_first.wait()
        return True

    mgr._route_microphone_audio = AsyncMock(side_effect=route)
    first = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [1] * 160},
            ingress_token=token,
        )
    )
    await asyncio.wait_for(first_started.wait(), 1)
    flush = asyncio.create_task(LLMSessionManager._flush_hot_swap_audio_cache(mgr))
    await asyncio.sleep(0)
    second = asyncio.create_task(
        LLMSessionManager._process_microphone_stream_data(
            mgr,
            {"input_type": "audio", "sample_rate_hz": 16_000, "data": [2] * 160},
            ingress_token=token,
        )
    )
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second, flush), 1)

    # The inflight live frame is routed first; the cached frames follow in
    # order (the native replay may coalesce them into a single send).
    assert routed[0][:2] == b"\x20\x00"
    assert b"".join(routed) == (
        b"\x20\x00" * 160 + b"\x10\x00" * 160 + b"\x30\x00" * 160
    )
    mgr._asr_runtime.abort.assert_not_awaited()
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()
    assert not mgr.hot_swap_audio_cache


async def test_hot_swap_mid_batch_failure_invalidates_candidate():
    mgr = _make_routable_audio_manager(True)
    token = _queue_token()
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(
        HotSwapAudioFrame(pcm16=b"\x10\x00" * 160, token=token)
    )
    assert mgr.hot_swap_audio_cache.append(
        HotSwapAudioFrame(pcm16=b"\x20\x00" * 160, token=token)
    )
    mgr._route_microphone_audio = AsyncMock(side_effect=RuntimeError("route failed"))

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._route_microphone_audio.assert_awaited_once()
    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()
    assert not mgr.hot_swap_audio_cache
    assert mgr.is_flushing_hot_swap_cache is False


class _FakeClock:
    """Module-local stand-in for ``time`` inside ``core_asr_runtime_module``."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


async def test_hot_swap_flush_hands_off_sustained_arrival_without_abort(
    monkeypatch,
):
    mgr = _make_routable_audio_manager(True)
    del mgr._route_microphone_audio
    token = mgr._capture_ingress_token()
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    for _ in range(40):
        assert mgr.hot_swap_audio_cache.append(
            HotSwapAudioFrame(pcm16=b"\x01\x00" * 160, token=token)
        )
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def paced_arrival_sleep(delay: float) -> None:
        # Sustained live ingress: ~2 frames arrive during each 25 ms pacing
        # gap, so a paced drain settles at a small steady state instead of
        # converging to empty on its own.
        sleeps.append(delay)
        for _ in range(2):
            assert mgr.hot_swap_audio_cache.append(
                HotSwapAudioFrame(pcm16=b"\x01\x00" * 160, token=token)
            )
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", paced_arrival_sleep)

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    # One paced pass (40 frames, 8 batches) plus the unpaced tail handoff
    # (16 frames that arrived during pacing); nothing is damaged.
    assert sleeps == [0.025] * 8
    sent = b"".join(
        call.args[0] for call in mgr.session.stream_audio.await_args_list
    )
    assert sent == b"\x01\x00" * 160 * 56
    mgr._asr_runtime.abort.assert_not_awaited()
    assert not mgr.hot_swap_audio_cache
    assert mgr.is_flushing_hot_swap_cache is False
    assert mgr.is_hot_swap_imminent is False


async def test_hot_swap_flush_deadline_invalidates_non_converging_replay(
    monkeypatch,
):
    mgr = _make_routable_audio_manager(True)
    token = _queue_token()
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    for _ in range(40):
        assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(token))
    clock = _FakeClock()
    monkeypatch.setattr(core_asr_runtime_module, "time", clock)
    real_sleep = asyncio.sleep

    async def degraded_sleep(delay: float) -> None:
        # Ingress matches the replay rate exactly (5 frames per 25 ms
        # pacing gap), so the backlog never shrinks below the handoff
        # threshold: genuine backpressure, not a healthy steady state.
        clock.now += delay
        for _ in range(5):
            assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(token))
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", degraded_sleep)

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")
    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()
    assert not mgr.hot_swap_audio_cache
    assert mgr.is_flushing_hot_swap_cache is False
    assert mgr.is_hot_swap_imminent is False


async def test_native_route_skips_send_after_fatal_error_with_rate_limited_log(
    monkeypatch,
):
    mgr = _make_routable_audio_manager(True)
    mgr.session._fatal_error_occurred = True
    token = mgr._capture_ingress_token()
    log_warning = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "warning", log_warning)

    await LLMSessionManager._route_microphone_audio(
        mgr,
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        ingress_token=token,
    )
    await LLMSessionManager._route_microphone_audio(
        mgr,
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        ingress_token=token,
    )

    mgr.session.stream_audio.assert_not_awaited()
    mgr._record_omni_microphone_audio.assert_not_called()
    log_warning.assert_called_once()


async def test_hot_swap_flush_batches_and_paces_native_replay(monkeypatch):
    mgr = _make_routable_audio_manager(True)
    del mgr._route_microphone_audio
    token = mgr._capture_ingress_token()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    # 27 frames (270 ms) exceeds the 250 ms tail-handoff threshold, so the
    # first pass replays with batching and pacing.
    for _ in range(27):
        assert mgr.hot_swap_audio_cache.append(
            HotSwapAudioFrame(pcm16=b"\x01\x00" * 160, token=token)
        )
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    sent = [call.args[0] for call in mgr.session.stream_audio.await_args_list]
    assert sent == [b"\x01\x00" * 160 * 5] * 5 + [b"\x01\x00" * 160 * 2]
    assert sleeps == [0.025] * 6
    assert not mgr.hot_swap_audio_cache


async def test_hot_swap_flush_bursts_small_tail_without_pacing(monkeypatch):
    mgr = _make_routable_audio_manager(True)
    del mgr._route_microphone_audio
    token = mgr._capture_ingress_token()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    for _ in range(7):
        assert mgr.hot_swap_audio_cache.append(
            HotSwapAudioFrame(pcm16=b"\x01\x00" * 160, token=token)
        )
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    sent = [call.args[0] for call in mgr.session.stream_audio.await_args_list]
    assert sent == [b"\x01\x00" * 160 * 5, b"\x01\x00" * 160 * 2]
    assert sleeps == []
    assert not mgr.hot_swap_audio_cache


async def test_hot_swap_flush_counts_and_logs_unrebindable_frames(monkeypatch):
    mgr = _make_routable_audio_manager(True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(_queue_token()))
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(_queue_token()))
    log_warning = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "warning", log_warning)

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    assert mgr._audio_stream_dropped_total == 2
    log_warning.assert_called_once()
    mgr._route_microphone_audio.assert_not_awaited()
    mgr._asr_runtime.abort.assert_not_awaited()
    assert not mgr.hot_swap_audio_cache


async def test_hot_swap_flush_aborts_once_for_multiple_damaged_tokens():
    mgr = _make_routable_audio_manager(True)
    mgr._ingress_token_matches = MagicMock(return_value=True)
    mgr._asr_runtime.abort = AsyncMock()
    mgr.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
    token_a = VoiceIngressToken(1, "socket", 1, 1, 1)
    token_b = VoiceIngressToken(1, "socket", 1, 2, 1)
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(token_a))
    assert mgr.hot_swap_audio_cache.append(_hot_swap_frame(token_b))
    mgr._route_microphone_audio = AsyncMock(side_effect=RuntimeError("route failed"))

    await LLMSessionManager._flush_hot_swap_audio_cache(mgr)

    mgr._route_microphone_audio.assert_awaited_once()
    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")
    assert not mgr.hot_swap_audio_cache


async def test_slow_runtime_abort_does_not_block_enqueue_processing():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr._init_asr_runtime_state()
    _authorize_core_lease(mgr)
    mgr._audio_stream_queue = AudioDurationQueue(
        capacity_us=1_000_000,
        max_frames=1,
    )
    mgr._audio_stream_dropped_total = 0
    mgr._last_audio_stream_backlog_log_time = 0.0
    mgr._ensure_audio_stream_worker = lambda: None
    mgr._bg_tasks = set()
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def slow_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    mgr._asr_runtime.abort = AsyncMock(side_effect=slow_abort)
    message = {
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [1] * 160,
    }

    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)
    # Overflow: the teardown is scheduled off the receive path, so this call
    # returns without waiting for the slow provider close.
    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)
    assert mgr._audio_stream_queue.empty()
    await asyncio.wait_for(abort_started.wait(), 1)
    assert not release_abort.is_set()

    # A later frame is accepted while the abort is still pending.
    await LLMSessionManager._enqueue_audio_stream_data(mgr, message)
    assert mgr._audio_stream_queue.qsize() == 1

    release_abort.set()
    await asyncio.gather(*list(mgr._bg_tasks))
    mgr._asr_runtime.abort.assert_awaited_once_with("ingress_backpressure")

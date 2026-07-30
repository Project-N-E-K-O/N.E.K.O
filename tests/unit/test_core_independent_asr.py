import asyncio
import inspect
import json
import logging
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from main_logic.core import LLMSessionManager
from main_logic.core.asr_runtime import AsrRuntimeMixin, _HotSwapAudioFrame
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
from main_logic.asr_client.detector_runtime import DetectorFeedResult, DetectorRuntime
from main_logic.voice_input import VoiceInputDispatchResult
from main_logic.asr_client.lifecycle import (
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceTurnToken,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    SpeechActivityEvent,
    VoicePartialEvent,
    VoiceTranscriptEvent,
)
from main_logic.voice_turn.contracts import EvaluationStatus, TurnDecision
from main_logic.voice_turn.coordinator import CoordinatorState
from main_logic.asr_client.detector import (
    BoundDetectorTurn,
    CoreDetectorEventEnvelope,
    DetectorCandidateKey,
    DetectorIngressIdentity,
    DetectorRuntimeEvent,
    DetectorTurnEvent,
    DetectorSubmitResult,
    DetectorSubmitStatus,
)
import main_logic.core.asr_runtime as core_asr_runtime_module
import main_logic.core as core_module
import main_logic.voice_turn.audio_input as audio_input_module
from utils import preferences


pytestmark = pytest.mark.asyncio


class _Runtime(AsrRuntimeMixin):
    def __init__(self) -> None:
        self._init_asr_runtime_state()
        self._voice_lease_synchronized = True
        self._voice_lease_owner = "core"
        self._voice_input_suppressed = False
        self.lanlan_name = "Test"
        self.session = type("Omni", (), {})()
        self.session.create_response = AsyncMock()
        self.session.handle_interruption = AsyncMock()
        self.handle_new_message = AsyncMock()
        self.handle_input_transcript = AsyncMock(return_value=True)
        self.send_status = AsyncMock()

    def __getattr__(self, name: str):
        component = self.__dict__.get("_asr_runtime")
        if component is not None and hasattr(component, name):
            return getattr(component, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        component = self.__dict__.get("_asr_runtime")
        if name in {
            "_asr_route_mode",
            # Keep the operation generation on the instance so reads observe
            # bumps, matching production (writes routed to the component
            # would freeze it at its initial value for every read).
            "_asr_route_operation_generation",
            "_microphone_route_generation",
            "_independent_asr_provider",
            "_independent_asr_route_key",
            "_voice_input_audio_pipeline",
        }:
            object.__setattr__(self, name, value)
            return
        if component is not None and (
            name.startswith("_asr_")
            or name
            in {
                "_voice_input_resource_optimization_enabled",
            }
        ):
            setattr(component, name, value)
            if name == "_asr_lifecycle" and value is not None:
                component._asr_current_ingress_token = self._capture_ingress_token()
            return
        object.__setattr__(self, name, value)


class _TestSmartTurnLease:
    def __init__(self, token) -> None:
        self.token = token
        self.released = False

    async def release(self) -> None:
        self.released = True


class _ReadyDetector:
    def __init__(self, feed_result: DetectorFeedResult | None = None) -> None:
        self._token = None
        self._feed_result = feed_result or DetectorFeedResult((), True)
        self.reset = AsyncMock(side_effect=self._reset)
        self.close = AsyncMock()
        self.release_deferred_turn = AsyncMock()

    async def prepare_endpointing(self, token):
        self._token = token
        return _TestSmartTurnLease(token)

    def endpointing_ready(self, token) -> bool:
        return self._token == token

    async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
        return self._feed_result

    async def _reset(self) -> None:
        self._token = None


class _FailedSmartTurnDetector(_ReadyDetector):
    async def prepare_endpointing(self, token):
        self._token = None
        return None

    def endpointing_ready(self, token) -> bool:
        return False


class _QueuedSmartTurnDetector(_ReadyDetector):
    def __init__(self) -> None:
        super().__init__()
        self.queued_audio_ms = 0
        self.smart_turn_evaluation_ms = 0
        self.smart_turn_stale_result_count = 0
        self.smart_turn_coalesced_evaluation_count = 0
        self.force_speech_started = AsyncMock(return_value=True)
        self._sequence_no = 0

    async def submit_audio(
        self,
        _pcm16: bytes,
        *,
        ingress_token,
        **_kwargs,
    ) -> DetectorSubmitResult:
        self._sequence_no += 1
        return DetectorSubmitResult(
            status=DetectorSubmitStatus.ACCEPTED,
            throttle_available=False,
            endpointing_available=True,
            identity=DetectorIngressIdentity(
                ingress_token=ingress_token,
                detector_epoch=1,
                sequence_no=self._sequence_no,
            ),
        )


def _selection(provider_key: str, endpointing_mode: str = "manual"):
    return type(
        "Selection",
        (),
        {
            "provider_key": provider_key,
            "endpointing_mode": endpointing_mode,
            "soniox_region": None,
        },
    )()


async def _start_runtime_with_callback_candidates(
    monkeypatch,
    *,
    candidate_count: int = 2,
):
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    selection = _selection("qwen", "provider")
    selection_ref = selection
    detector = _ReadyDetector()
    callbacks: list[dict[str, object]] = []
    sessions = [
        SimpleNamespace(
            is_ready=True,
            connect=AsyncMock(),
            close=AsyncMock(),
        )
        for _ in range(candidate_count)
    ]

    def create_candidate(_core_type, *, selection: object, **kwargs):
        assert selection is selection_ref
        callbacks.append(kwargs)
        return sessions[len(callbacks) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": False,
            }
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(return_value=detector),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_session is sessions[0]
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._asr_detector is detector
    assert runtime._asr_route_mode == "independent"
    return runtime, sessions, callbacks, detector


def _install_ready_lifecycle(
    runtime: _Runtime,
    provider: str = "qwen",
) -> None:
    if runtime._asr_session is None:
        runtime._asr_session = type("Asr", (), {"is_ready": True})()
    runtime._asr_provider = provider
    runtime._set_microphone_route("independent")
    endpointing_mode = "provider" if provider == "openai" else "manual"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, endpointing_mode),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()


def _install_replacement_runtime_generation(
    runtime: _Runtime,
    provider: str = "qwen",
):
    component = runtime._asr_runtime
    component._asr_session_epoch += 1
    component._asr_audio_generation += 1
    component._asr_transcript_dispatcher.invalidate_all()
    component._asr_detector_dispatcher.invalidate_all()
    component._asr_audio_dispatcher.abort()
    session = SimpleNamespace(
        is_ready=True,
        close=AsyncMock(),
        signal_user_activity_end=AsyncMock(),
    )
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _QueuedSmartTurnDetector()
    detector.detector_epoch = 1
    component._asr_session = session
    component._asr_provider = provider
    component._asr_lifecycle = lifecycle
    component._asr_detector = detector
    runtime._set_microphone_route("independent")
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    return session, lifecycle, detector


async def _install_active_smart_turn(runtime: _Runtime, provider: str = "qwen") -> None:
    _install_ready_lifecycle(runtime, provider)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )


async def _start_and_seal_turn(
    runtime: _Runtime,
    provider: str = "qwen",
) -> None:
    if runtime._asr_lifecycle is None:
        _install_ready_lifecycle(runtime, provider)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )
    await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)


async def test_independent_route_sends_pcm_to_asr_only() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_audio_bytes == 320
    assert runtime._omni_mic_audio_bytes == 0


async def test_stale_submit_drops_only_current_frame() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.STALE)
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "independent"


async def test_unavailable_submit_blocks_core_route() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
    )
    clear_queue = MagicMock(wraps=runtime._clear_audio_stream_queue)
    clear_cache = MagicMock(wraps=runtime.hot_swap_audio_cache.clear)
    runtime._clear_audio_stream_queue = clear_queue
    runtime.hot_swap_audio_cache.clear = clear_cache

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert runtime._asr_route_mode == "blocked"
    clear_queue.assert_called_once_with("independent_asr_unavailable")
    clear_cache.assert_called_once_with()


async def test_stale_unavailable_submit_cannot_block_replacement_route() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    submit_started = asyncio.Event()
    release_submit = asyncio.Event()

    async def unavailable_after_replacement(*_args, **_kwargs):
        submit_started.set()
        await release_submit.wait()
        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)

    runtime._asr_runtime.submit = AsyncMock(side_effect=unavailable_after_replacement)
    clear_queue = MagicMock(wraps=runtime._clear_audio_stream_queue)
    clear_cache = MagicMock(wraps=runtime.hot_swap_audio_cache.clear)
    runtime._clear_audio_stream_queue = clear_queue
    runtime.hot_swap_audio_cache.clear = clear_cache
    routed = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.wait_for(submit_started.wait(), 1)

    new_core_session = SimpleNamespace(stream_audio=AsyncMock())
    new_asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime.session = new_core_session
    runtime._asr_runtime._asr_audio_generation += 1
    runtime._asr_session = new_asr_session
    runtime._independent_asr_provider = "provider-b"
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    release_submit.set()
    await asyncio.wait_for(routed, 1)

    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "provider-b"
    assert runtime.session is new_core_session
    assert runtime._asr_session is new_asr_session
    clear_queue.assert_not_called()
    clear_cache.assert_not_called()


async def test_async_detector_orders_pre_roll_before_smart_turn_seal() -> None:
    class Vad:
        def load(self) -> bool:
            return True

        def close(self) -> None:
            return None

    class Gate:
        def feed(self, _pcm16: bytes):
            return (
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.CANDIDATE_PAUSE,
            )

        def reset(self) -> None:
            return None

    class Coordinator:
        state = CoordinatorState.IDLE

        def push_audio(self, _pcm16: bytes) -> None:
            return None

        async def on_activity_event(self, event) -> None:
            self.state = (
                CoordinatorState.PAUSE_CANDIDATE
                if event is SpeechActivityEvent.CANDIDATE_PAUSE
                else CoordinatorState.SPEECH_ACTIVE
            )

        async def evaluate_buffered(self):
            return SimpleNamespace(
                status=EvaluationStatus.OK,
                decision=TurnDecision.COMPLETE,
            )

        async def prepare_predictor(self) -> bool:
            return True

        async def reset(self) -> None:
            self.state = CoordinatorState.IDLE

        async def close(self) -> None:
            self.state = CoordinatorState.CLOSED

        async def unload_predictor(self) -> None:
            return None

    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.signal_user_activity_end = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle = lifecycle
    detector: DetectorRuntime

    async def on_event(event) -> None:
        assert runtime._asr_detector_dispatcher.submit_nowait(
            CoreDetectorEventEnvelope(
                event=event,
                detector_ref=detector,
                lifecycle_ref=lifecycle,
                session_epoch=runtime._asr_session_epoch,
            )
        )

    detector = DetectorRuntime(
        vad=Vad(),
        gate=Gate(),
        provider_policy=resolve_provider_policy("qwen", "manual"),
        coordinator=Coordinator(),
        on_event=on_event,
    )
    runtime._asr_detector = detector
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    for _ in range(200):
        if asr.signal_user_activity_end.await_count:
            break
        await asyncio.sleep(0.001)
    await runtime._asr_detector_dispatcher.wait_idle()
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(pcm16, sample_rate_hz=16_000)
    asr.signal_user_activity_end.assert_awaited_once()
    assert runtime._omni_mic_audio_bytes == 0
    await detector.close()


@pytest.mark.parametrize(
    "provider",
    ["dummy", "glm", "gemini"],
)
async def test_smart_turn_unavailable_blocks_segmented_provider_before_wire_audio(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(
            provider,
            "manual",
        ),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("provider", ["qwen", "grok", "soniox"])
async def test_provider_endpoint_does_not_wait_for_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _FailedSmartTurnDetector(
        DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    pcm16 = b"\x01\x00" * 160

    assert await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(pcm16, sample_rate_hz=16_000)
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0


async def test_enforced_lifecycle_suppresses_local_silence_upload() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = type(
        "Detector",
        (),
        {"feed": AsyncMock(return_value=DetectorFeedResult((), True))},
    )()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 320


async def test_local_speech_wake_uploads_pre_roll_to_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(
        side_effect=[
            DetectorFeedResult((), True),
            DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True),
        ]
    )
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(
        (b"\x01\x00" * 160) + (b"\x02\x00" * 160),
        sample_rate_hz=16_000,
    )
    runtime.session.handle_interruption.assert_awaited_once_with()


async def test_detector_failure_fails_open_to_same_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime)
    runtime._asr_detector.feed = AsyncMock(return_value=DetectorFeedResult((), False))

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once_with(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_route_mode == "independent"


async def test_game_takeover_clears_provider_audio_and_suspends_lifecycle() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {"reset": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._suspend_independent_voice_input_for_game()

    asr.close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"

    await runtime._resume_independent_voice_input_after_game()
    assert runtime._asr_lifecycle.snapshot.state.value == "local_listen"


async def test_game_takeover_wins_even_if_provider_clear_fails() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock(side_effect=RuntimeError("provider abort failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    await runtime._suspend_independent_voice_input_for_game()

    assert runtime._asr_lifecycle.snapshot.state.value == "suspended"


async def test_game_consumer_reuses_smart_turn_asr_without_core(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True

    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_awaited_once_with(
        "Test",
        "play",
        request_id=f"asr-{epoch}-1",
    )
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            2,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )


async def test_game_consumer_ignores_empty_final(monkeypatch) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    event = VoiceTranscriptEvent(
        turn_token=runtime._asr_runtime._capture_turn_token(lifecycle),
        provider="qwen",
        text="",
    )

    assert await runtime._prepare_voice_input_turn(event.turn_token) is True
    await runtime._dispatch_voice_input_final(event)
    await runtime._voice_input_registry.wait_idle()

    route_transcript.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_game_takeover_pre_abort_window_rejects_stale_core_turn(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    runtime.session.abandon_external_voice_turn = MagicMock()
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    _install_ready_lifecycle(runtime, "openai")
    runtime._asr_session.close = AsyncMock()
    runtime._asr_session.signal_user_activity_end = AsyncMock()

    preview_clear_started = asyncio.Event()
    release_preview_clear = asyncio.Event()

    async def block_preview_clear(payload: dict[str, object]) -> None:
        if (
            payload.get("type") == "user_transcript_preview"
            and payload.get("text") == ""
        ):
            preview_clear_started.set()
            await release_preview_clear.wait()

    runtime.websocket = SimpleNamespace(
        send_json=AsyncMock(side_effect=block_preview_clear),
    )
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "openai")
    sealed = runtime._asr_runtime._asr_sealed_turn_token
    assert sealed is not None
    stale_ingress = sealed.turn.ingress

    await runtime._handle_independent_asr_final("", epoch, "openai")
    await asyncio.wait_for(preview_clear_started.wait(), 1)

    takeover = asyncio.create_task(
        runtime._handle_voice_input_control("game_takeover", 2)
    )
    endpoint: asyncio.Task[None] | None = None
    try:
        for _ in range(100):
            if runtime._voice_lease_owner == "game":
                break
            await asyncio.sleep(0)
        assert runtime._voice_lease_owner == "game"
        assert runtime._voice_lease_generation == 2
        assert takeover.done() is False

        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        prepared_token = runtime._asr_runtime._asr_partial_turn_token
        endpoint = asyncio.create_task(
            runtime._handle_independent_asr_endpoint(epoch)
        )
        for _ in range(100):
            if endpoint.done():
                break
            await asyncio.sleep(0)
        await runtime._handle_independent_asr_final(
            "stale core audio",
            epoch,
            "openai",
        )
        await runtime._asr_runtime.wait_transcript_idle()

        assert stale_ingress.lease_generation == 1
        assert prepared_token is None
        route_transcript.assert_not_awaited()
    finally:
        release_preview_clear.set()
        pending = [takeover]
        if endpoint is not None:
            pending.append(endpoint)
        results = await asyncio.wait_for(asyncio.gather(*pending), 1)
        assert results[0] is True
        await runtime._voice_input_registry.wait_idle()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-1"
    )


async def test_rejected_voice_input_final_is_observable(monkeypatch) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    event = VoiceTranscriptEvent(
        turn_token=runtime._asr_runtime._capture_turn_token(
            runtime._asr_lifecycle
        ),
        provider="qwen",
        text="hello",
    )
    runtime._voice_input_registry.dispatch_final = AsyncMock(
        return_value=VoiceInputDispatchResult.REJECTED
    )
    debug = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "debug", debug)

    await runtime._dispatch_voice_input_final(event)

    debug.assert_called_once()
    assert "voice input final rejected" in debug.call_args.args[0]


async def test_game_consumer_accepts_real_pcm_through_pipeline(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    route_audio = AsyncMock(return_value=True)
    runtime._route_microphone_audio = route_audio
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.8,
        rnnoise_available=True,
    )
    runtime._voice_input_audio_pipeline.process = AsyncMock(return_value=processed)
    token = runtime._capture_ingress_token()

    await runtime._process_microphone_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        },
        ingress_token=token,
    )

    runtime._voice_input_audio_pipeline.process.assert_awaited_once()
    route_audio.assert_awaited_once_with(
        processed.pcm16,
        sample_rate_hz=processed.sample_rate_hz,
        speech_probability=processed.speech_probability,
        rnnoise_available=processed.rnnoise_available,
        ingress_token=token,
    )


async def test_game_consumer_submit_preserves_owner_identity(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "qwen"
    runtime._asr_runtime.submit = AsyncMock(
        return_value=AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
    )
    token = runtime._capture_ingress_token()
    processed = ProcessedVoiceFrame(
        pcm16=b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        speech_probability=0.8,
        rnnoise_available=True,
    )

    await runtime._route_microphone_audio(
        processed.pcm16,
        sample_rate_hz=processed.sample_rate_hz,
        speech_probability=processed.speech_probability,
        rnnoise_available=processed.rnnoise_available,
        ingress_token=token,
    )

    runtime._asr_runtime.submit.assert_awaited_once_with(
        processed,
        ingress_token=token,
    )


async def test_game_consumer_failure_never_falls_back_to_core(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(side_effect=RuntimeError("consumer failed"))
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final("play", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_awaited_once_with(
        "Test",
        "play",
        request_id=f"asr-{epoch}-1",
    )
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_game_final_cannot_cross_lease_back_to_core(monkeypatch) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_voice_input_control(
        "lease_sync",
        2,
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
    )
    await runtime._handle_independent_asr_final("stale", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_hard_mute_overrides_game_consumer(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )

    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=True,
        focus_suppressed=False,
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._voice_input_suppression_reasons == {"hard_mute"}
    assert runtime._omni_mic_audio_bytes == 0


async def test_game_owner_without_consumer_remains_fail_closed() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
    assert runtime._omni_mic_audio_bytes == 0


async def test_fresh_blocked_route_consumes_pcm_without_omni() -> None:
    runtime = _Runtime()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


async def test_native_route_is_sufficient_to_authorize_omni_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_route_mode == "native"
    runtime.session.stream_audio.assert_awaited_once()
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_speech_started_interrupts_and_prepares_turn_once() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    runtime.session.handle_interruption.assert_awaited_once_with()
    runtime.handle_new_message.assert_awaited_once_with()
    assert runtime._asr_turn_prepared is True


async def test_speech_started_prepares_external_voice_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    runtime.session.prepare_external_voice_turn.assert_awaited_once_with(
        turn_id=f"asr-{runtime._asr_session_epoch}-1"
    )
    runtime.handle_new_message.assert_awaited_once_with()


async def test_game_takeover_during_core_prepare_drops_stale_message() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    async def block_prepare(*, turn_id: str) -> None:
        prepare_started.set()
        await release_prepare.wait()

    runtime.session.prepare_external_voice_turn = AsyncMock(side_effect=block_prepare)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime._asr_runtime.suspend = AsyncMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    prepare_task = asyncio.create_task(runtime._prepare_core_voice_turn(token))
    await asyncio.wait_for(prepare_started.wait(), 1)

    await runtime._suspend_independent_voice_input_for_game()
    release_prepare.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    runtime.handle_new_message.assert_not_awaited()
    external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    assert runtime.session.abandon_external_voice_turn.call_args_list == [
        call(external_turn_id),
    ]


async def test_stale_core_prepare_restores_previous_preview_owner() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    async def block_prepare(*, turn_id: str) -> None:
        del turn_id
        prepare_started.set()
        await release_prepare.wait()

    runtime.session.prepare_external_voice_turn = AsyncMock(side_effect=block_prepare)
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    previous_token = replace(token, turn_id=token.turn_id + 100)
    previous_turn_id = (
        f"asr-{previous_token.ingress.session_epoch}-{previous_token.turn_id}"
    )
    runtime._core_asr_preview_turn_id = previous_turn_id
    runtime._core_asr_preview_turn_token = previous_token
    runtime._core_asr_preview_text = "previous partial"

    prepare_task = asyncio.create_task(runtime._prepare_core_voice_turn(token))
    await asyncio.wait_for(prepare_started.wait(), 1)
    runtime._voice_input_transition_generation += 1
    release_prepare.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    assert runtime._core_asr_preview_turn_id == previous_turn_id
    assert runtime._core_asr_preview_turn_token == previous_token
    assert runtime._core_asr_preview_text == "previous partial"
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_turn_endpoint_seals_immediately_before_provider_final() -> None:
    runtime = _Runtime()
    runtime._asr_session = type("Asr", (), {"is_ready": True})()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    await runtime._handle_independent_asr_endpoint(epoch)

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING


async def test_rejected_prepare_fails_closed_instead_of_sealing_turn() -> None:
    runtime = _Runtime()
    runtime._asr_session = type(
        "Asr", (), {"is_ready": True, "close": AsyncMock()}
    )()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message.side_effect = RuntimeError("prepare rejected")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE

    await runtime._handle_independent_asr_endpoint(epoch)

    # A persistently rejected preparation must never seal the turn: sealing
    # is the only gate through which a provider final reaches Core, and Core
    # does not re-run the interruption/external-turn pause at dispatch time.
    assert runtime._asr_route_mode == "blocked"
    assert "ASR_CORE_TURN_REJECTED" in str(runtime.send_status.await_args_list)

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_endpoint_reprepares_turn_after_transient_prepare_rejection() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message.side_effect = [RuntimeError("transient"), None]
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE

    await runtime._handle_independent_asr_endpoint(epoch)

    # The retry-able recovery path: the endpoint re-runs preparation, so the
    # interruption/external-turn pause is established before the seal and the
    # provider final is injected normally.
    assert runtime._asr_turn_prepared is True
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime.handle_new_message.await_count == 2

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once()
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_empty_final_completes_turn_without_core_injection() -> None:
    runtime = _Runtime()
    runtime.session.prepare_external_voice_turn = AsyncMock()
    runtime.session.abandon_external_voice_turn = MagicMock()
    await _start_and_seal_turn(runtime)
    turn_id = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]

    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )
    # Teardown racing the queued empty final may win or lose, but both paths
    # terminate the same pinned route. Repeated invalidation and a duplicate
    # provider final must not produce a second cancellation/abandonment.
    runtime._invalidate_voice_pcm_sync("duplicate_after_empty_final")
    runtime._invalidate_voice_pcm_sync("duplicate_after_empty_final")
    await runtime._handle_independent_asr_final(
        "",
        runtime._asr_session_epoch,
        "qwen",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_lifecycle.metrics.false_wake_count == 1
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(turn_id)
    assert runtime._omni_mic_audio_bytes == 0


async def test_blocked_consumer_callback_does_not_block_next_turn_lifecycle() -> (
    None
):
    runtime = _Runtime()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def block_first_final(*_args, **_kwargs) -> bool:
        callback_started.set()
        await release_callback.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_first_final
    await _start_and_seal_turn(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await asyncio.wait_for(callback_started.wait(), 1)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_turn_prepared is True
    release_callback.set()
    await runtime._wait_asr_transcript_dispatch_idle()
    runtime.session.create_response.assert_awaited_once_with("first")


async def test_prepare_failure_releases_keyed_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock(
        side_effect=RuntimeError("prepare failed")
    )
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    assert await runtime._prepare_core_voice_turn(token) is False

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_registry_prepare_rejection_releases_keyed_external_turn_pause() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message = AsyncMock(side_effect=RuntimeError("history failed"))
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    assert await runtime._prepare_voice_input_turn(token) is False

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_registry_cancelled_prepare_releases_keyed_external_turn_pause() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message = AsyncMock(side_effect=asyncio.CancelledError)
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    with pytest.raises(asyncio.CancelledError):
        await runtime._prepare_voice_input_turn(token)
    await runtime._voice_input_registry.wait_idle()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_final_transcript_submits_to_the_session_it_was_validated_against() -> None:
    # Codex P2. _dispatch_core_asr_transcript validates `self.session is
    # session_ref`, then awaits _restore_core_asr_preview_after_final -- a
    # websocket send. _submit_core_voice_turn used to re-read self.session after
    # that await, discarding the validation: a hot swap promoting a replacement
    # session inside the window made it inject this conversation's transcript
    # into the next one, producing a reply in the wrong conversation.
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    timed_session = runtime.session

    replacement = type("Omni", (), {})()
    replacement.create_response = AsyncMock()
    replacement.submit_external_voice_turn = AsyncMock()
    replacement.abandon_external_voice_turn = MagicMock()

    async def _hot_swap_mid_restore(*_args, **_kwargs) -> None:
        runtime.session = replacement

    runtime._restore_core_asr_preview_after_final = _hot_swap_mid_restore

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello"),
    )

    # CodeRabbit: the race is manufactured inside a hook, so if that hook ever
    # stops being called -- preview restore skipped, moved, or bypassed on the
    # accepted branch -- runtime.session would never move, and all three
    # assertions below would pass while modelling an ordinary final with no hot
    # swap at all. Pin that the swap really happened first.
    assert runtime.session is replacement
    # The turn lands on the session that produced it...
    timed_session.create_response.assert_awaited_once_with("hello")
    # ...and never touches the one that replaced it.
    replacement.create_response.assert_not_awaited()
    replacement.submit_external_voice_turn.assert_not_awaited()


async def test_pre_dispatch_session_swap_keeps_prepared_turn_on_pinned_session() -> None:
    """A same-route hot swap must not discard an already-prepared utterance."""
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    prepared_session = runtime.session

    replacement = type("Omni", (), {})()
    replacement.create_response = AsyncMock()
    replacement.submit_external_voice_turn = AsyncMock()
    replacement.abandon_external_voice_turn = MagicMock()

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    runtime.session = replacement
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="prepared"),
        session_ref=prepared_session,
    )

    prepared_session.create_response.assert_awaited_once_with("prepared")
    replacement.create_response.assert_not_awaited()
    replacement.submit_external_voice_turn.assert_not_awaited()


async def test_final_transcript_is_dropped_when_the_route_leaves_core_mid_restore() -> None:
    # Codex P2, the other half of the case above. Pinning session_ref protects
    # only the SESSION: a game or text takeover landing inside the preview
    # restore's websocket send moves _voice_lease_owner off "core" WITHOUT
    # necessarily replacing self.session, and the transcript was still injected
    # and an ordinary Core response started after the route had left Core.
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    timed_session = runtime.session

    takeover_ran = False

    async def _game_takeover_mid_restore(*_args, **_kwargs) -> None:
        nonlocal takeover_ran
        takeover_ran = True
        runtime._voice_lease_owner = "game"

    runtime._restore_core_asr_preview_after_final = _game_takeover_mid_restore

    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    await runtime._dispatch_core_asr_transcript(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello"),
    )

    # Pin from OUTSIDE the hook that the race was actually manufactured; without
    # this the case degrades into an ordinary final that never left Core.
    assert takeover_ran
    assert runtime._voice_lease_owner == "game"
    # Session identity never moved, so only the route check can catch this.
    assert runtime.session is timed_session
    # No Core response is started for a route that has moved on.
    timed_session.create_response.assert_not_awaited()


async def test_transcript_dispatch_failure_releases_keyed_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_input_transcript.side_effect = RuntimeError("history failed")
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=token,
        provider="qwen",
        text="hello",
    )

    with pytest.raises(RuntimeError, match="history failed"):
        await runtime._dispatch_core_asr_transcript(event)

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


@pytest.mark.parametrize("stale_guard", ["ingress", "owner"])
async def test_stale_final_guard_releases_keyed_external_turn_pause(
    stale_guard: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=token,
        provider="qwen",
        text="hello",
    )
    if stale_guard == "ingress":
        runtime._asr_audio_generation += 1
    else:
        runtime._voice_lease_owner = "game"

    await runtime._dispatch_core_asr_transcript(event)

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_abort_bumps_generation_before_waiting_for_registry_cancel() -> None:
    runtime = _Runtime()
    order: list[str] = []
    runtime._asr_runtime.abort = AsyncMock(
        side_effect=lambda _reason: order.append("abort")
    )
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._abort_independent_asr("ingress_backpressure")

    assert order == ["abort", "invalidate", "wait_idle"]


async def test_suspend_advances_runtime_barrier_before_waiting_for_registry_cancel() -> (
    None
):
    runtime = _Runtime()
    order: list[str] = []
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._asr_runtime.suspend = AsyncMock(
        side_effect=lambda _reason: order.append("suspend")
    )
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._suspend_independent_asr("game_takeover")

    assert order == ["suspend", "invalidate", "wait_idle"]


@pytest.mark.parametrize(
    ("previous_owner", "owner", "reason", "barrier_method"),
    [
        ("core", "game", "game_takeover", "suspend"),
        ("game", "core", "game_release", "abort"),
        ("core", "none", "connection_closed", "abort"),
    ],
)
async def test_voice_lease_advances_runtime_barrier_before_waiting_for_registry(
    previous_owner: str,
    owner: str,
    reason: str,
    barrier_method: str,
) -> None:
    runtime = _Runtime()
    runtime._voice_lease_owner = previous_owner
    order: list[str] = []
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._asr_runtime.suspend = AsyncMock(
        side_effect=lambda _reason: order.append("suspend")
    )
    runtime._asr_runtime.abort = AsyncMock(
        side_effect=lambda _reason: order.append("abort")
    )
    runtime._asr_runtime.resume = AsyncMock()
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._apply_voice_lease_state(
        owner=owner,
        hard_muted=False,
        focus_suppressed=False,
        reason=reason,
        force_abort=True,
    )

    assert order == ["invalidate", barrier_method, "wait_idle"]


@pytest.mark.parametrize("operation", ["abort", "close"])
async def test_core_asr_teardown_force_releases_external_turn_pause(
    operation: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    if operation == "abort":
        runtime._asr_runtime.abort = AsyncMock()
        await runtime._abort_independent_asr("test_abort")
        runtime._asr_runtime.abort.assert_awaited_once_with("test_abort")
    else:
        runtime._asr_runtime.close = AsyncMock()
        await runtime._close_independent_asr(next_route_mode="blocked")
        runtime._asr_runtime.close.assert_awaited_once_with()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )


async def test_current_asr_failure_force_releases_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )


async def test_registry_cancellation_abandons_the_prepared_session_after_swap() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    original_session = runtime.session
    original_session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    replacement = type("Omni", (), {})()
    replacement.abandon_external_voice_turn = MagicMock()
    runtime.session = replacement
    assert runtime._voice_input_registry.invalidate_utterance(
        token,
        reason="session_hot_swap",
    )
    await runtime._voice_input_registry.wait_idle()

    original_session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )
    replacement.abandon_external_voice_turn.assert_not_called()


async def test_runtime_close_preserves_manager_lifetime_registry_builtins() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    registry = runtime._voice_input_registry
    core_registration = runtime._core_chat_voice_input_registration
    game_registration = runtime._game_voice_input_registration
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True
    runtime._asr_runtime.close = AsyncMock()

    await runtime._close_independent_asr(next_route_mode="blocked")
    runtime._ensure_asr_runtime_state()
    runtime._ensure_asr_runtime_state()

    assert runtime._voice_input_registry is registry
    assert runtime._core_chat_voice_input_registration is core_registration
    assert runtime._game_voice_input_registration is game_registration
    assert core_registration.closed is False
    assert game_registration.closed is False
    assert len(registry._records) == 2


async def test_runtime_state_initializes_and_backfills_phase4a_fields() -> None:
    runtime = _Runtime()

    assert runtime._voice_input_resource_optimization_handshake_override is None
    assert runtime._voice_input_resource_optimization_session_value is None
    assert runtime._core_asr_preview_turn_token is None

    del runtime._voice_input_resource_optimization_handshake_override
    del runtime._voice_input_resource_optimization_session_value
    del runtime._core_asr_preview_turn_token
    runtime._ensure_asr_runtime_state()

    assert runtime._voice_input_resource_optimization_handshake_override is None
    assert runtime._voice_input_resource_optimization_session_value is None
    assert runtime._core_asr_preview_turn_token is None


async def test_provider_final_watchdog_blocks_only_independent_asr() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        provider_final_timeout_ms=10,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await _start_and_seal_turn(runtime)
    # 10ms 超时跟 Windows 的 15.6ms 定时器分辨率同量级，固定 sleep 到底睡多久
    # 完全看运气；守护任务跑完才是「已开火」的权威信号，直接等它。
    watchdog = runtime._asr_final_watchdog_task
    assert watchdog is not None
    await asyncio.wait_for(watchdog, 5)

    assert runtime._asr_route_mode == "blocked"
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_provider_final_watchdog_honors_per_provider_policy_timeout() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": True, "close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "glm"
    runtime._asr_route_mode = "independent"
    # Segmented providers resolve a longer final timeout than the streaming
    # default; scale both down so the watchdog must track the policy value.
    policy = replace(
        resolve_provider_policy("glm", "manual"),
        # 500ms 而不是 80ms：下面「还没到点」那半只能靠时间证明，被测窗口必须
        # 远大于 Windows 的 15.6ms 定时器分辨率，否则余量不到一个 tick。
        provider_final_timeout_ms=500,
    )
    assert resolve_provider_policy("glm", "manual").provider_final_timeout_ms == 40_000
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await _start_and_seal_turn(runtime, "glm")
    armed_at = time.monotonic()
    watchdog = runtime._asr_final_watchdog_task
    assert watchdog is not None

    # 单个 sleep 睡了多久在 Windows 上不可信（15.6ms 分辨率，既会提前弹出也会
    # 超发），所以只信真实时钟：轮询到确实过了 150ms —— 远超一个 tick，也远小于
    # 上面的 500ms 窗口。醒来后必须先复查挂钟再断言：这一觉可能被别的任务拖长而
    # 睡过了观察窗口（甚至睡过 500ms 守护窗口），那时守护任务改成 blocked 是合法的，
    # 先断言就会把「事后才发生」误报成「窗口内提前开火」。
    # A watchdog stuck on the shared default (10 ms in the scaled test above)
    # would have fired by now; the per-provider override keeps it armed.
    deadline = armed_at + 0.15
    while True:
        await asyncio.sleep(0.005)
        if time.monotonic() >= deadline:
            break
        assert runtime._asr_route_mode == "independent"

    # 正向那半不猜时间：守护任务自己跑完（内部 await 完错误处理才结束）即同步点。
    await asyncio.wait_for(watchdog, 5)
    elapsed = time.monotonic() - armed_at

    assert runtime._asr_route_mode == "blocked"
    # 上界也必须钉住，否则这条用例只主张「五秒内会开火」：把 per-provider 超时写成
    # 常量 2s、或者把 ms 当成 s 换算错的回归，在 150ms 观察窗口里同样还是
    # independent，然后在 5s 内跑完，照样通过。配置是 500ms，给 3 倍余量。
    assert elapsed < 1.5, (
        f"守护任务没有按 per-provider 的 500ms 超时开火，实际 {elapsed:.3f}s"
    )


async def test_optimization_disabled_continuously_uploads_with_smart_turn() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_detector.endpointing_ready(
        runtime._capture_turn_token(runtime._asr_lifecycle)
    )
    assert runtime._omni_mic_audio_bytes == 0


async def test_smart_turn_fail_open_advances_lifecycle_before_current_frame() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = "glm"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _QueuedSmartTurnDetector()
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    detector.force_speech_started.assert_awaited_once()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    asr.stream_audio.assert_awaited_once()
    assert runtime._asr_route_mode == "independent"


async def test_native_connection_close_is_latched_and_not_retried() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.last_audio_send_error_time = 0.0
    runtime.audio_error_log_interval = 2.0
    runtime.session.stream_audio = AsyncMock(
        side_effect=AttributeError("connection already closed")
    )

    assert (
        await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
        is True
    )
    assert (
        await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
        is True
    )

    assert runtime.session_closed_by_server is True
    runtime.session.stream_audio.assert_awaited_once()


async def test_native_audio_failure_log_is_rate_limited(monkeypatch) -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.last_audio_send_error_time = 0.0
    runtime.audio_error_log_interval = 2.0
    runtime.session.stream_audio = AsyncMock(side_effect=RuntimeError("send failed"))
    log_error = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "error", log_error)

    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)

    assert runtime.session.stream_audio.await_count == 2
    log_error.assert_called_once()


@pytest.mark.parametrize("provider", ["qwen", "openai"])
async def test_optimization_disabled_provider_route_never_prepares_smart_turn(
    provider: str,
) -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    asr = type("Asr", (), {"is_ready": True, "stream_audio": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_provider = provider
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "provider"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.prepare_endpointing = AsyncMock()
    runtime._asr_detector = detector

    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    asr.stream_audio.assert_awaited_once()
    detector.prepare_endpointing.assert_not_awaited()
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_smart_turn_lease is None
    assert runtime._omni_mic_audio_bytes == 0


async def test_draining_next_speech_waits_for_old_final_then_starts_new_turn() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    detector.reset = AsyncMock()
    detector.release_deferred_turn = AsyncMock()
    runtime._asr_detector = detector
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    old_turn = runtime._asr_lifecycle.identity.turn_id
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pending_turn_bytes == 320

    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_lifecycle.identity.turn_id == old_turn + 1
    asr.stream_audio.assert_awaited_once_with(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime.handle_new_message.await_count == 2
    detector.reset.assert_not_awaited()
    detector.release_deferred_turn.assert_awaited_once_with()

    runtime.handle_input_transcript.reset_mock()
    await runtime._handle_independent_asr_final("stale-old-turn", epoch, "qwen")
    runtime.handle_input_transcript.assert_not_awaited()


async def test_warm_idle_pending_speech_does_not_reenter_draining_guard() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert lifecycle.has_pending_turn is True

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    assert lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert lifecycle.has_pending_turn is True


async def test_stale_pending_activation_discards_confirmed_candidate() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._route_microphone_audio(
        b"\x02\x00" * 160,
        sample_rate_hz=16_000,
    )
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert lifecycle.has_pending_turn is True

    await runtime._activate_pending_independent_turn(epoch)

    assert lifecycle.pending_turn_bytes == 0
    assert lifecycle.has_pending_turn is False
    assert runtime._asr_pending_detector_candidate is None


async def test_final_without_observed_pending_preserves_racing_next_onset() -> None:
    runtime = _Runtime()
    await _start_and_seal_turn(runtime, "gemini")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)

    await runtime._handle_independent_asr_final(
        "first",
        runtime._asr_session_epoch,
        "gemini",
    )

    # A next onset may be admitted after final acceptance but before cleanup.
    # Releasing the completed turn preserves that audio; a full reset loses it.
    detector.reset.assert_not_awaited()
    detector.release_deferred_turn.assert_awaited_once_with()


async def test_active_onset_before_delayed_provider_final_starts_next_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is True

    # Provider VAD already ended turn 1, but its ordered endpoint callback is
    # delivered only right before the delayed final. The local detector sees
    # the next turn's onset while Core is still ACTIVE and prepared.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    # Turn 1's ordered callbacks arrive: endpoint immediately before final.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # The remembered onset was replayed: turn 2 is ACTIVE and prepared.
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_turn_prepared is True

    # Turn 2's ordered callbacks now seal and deliver instead of no-oping.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second"]
    assert runtime.handle_new_message.await_count == 2


async def test_stale_overlap_onset_is_not_replayed_after_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    # The audio generation moves on before the delayed final, so the recorded
    # onset belongs to a stale ingress and must not wake a replacement turn.
    component = runtime._asr_runtime
    component._asr_audio_generation += 1
    component._asr_current_ingress_token = runtime._capture_ingress_token()

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False
    # The prepared Registry route retains the original full VoiceTurnToken.
    # Rotating audio_generation makes the later final a different route, so
    # strict routing drops it together with the stale overlap onset.
    runtime.handle_input_transcript.assert_not_awaited()
    assert runtime.handle_new_message.await_count == 1


async def test_candidate_pause_defers_overlap_onset_without_ghost_wake() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    # Local VAD then observes a pause: the provider final that follows may be
    # the current utterance ending, so replaying the onset at that final would
    # wake a ghost turn. The onset converts into a completed-overlap credit
    # that only a later provider endpoint in WARM_IDLE can redeem.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_onset_token is None
    assert runtime._asr_overlap_completed_turns == 1

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("hello", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # No second endpoint arrived, so the credit must not wake anything.
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["hello"]
    assert runtime.handle_new_message.await_count == 1


async def test_completed_overlap_before_delayed_final_delivers_both_finals() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    assert runtime._asr_turn_prepared is True

    # Turn 2 both starts and reaches local silence while turn 1 is still
    # ACTIVE and prepared: its provider endpoint and final are queued in the
    # ordered FIFO behind turn 1's delayed final.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )

    # Turn 1's ordered callbacks arrive: endpoint immediately before final.
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # The completed overlap is not replayed yet: only turn 2's own provider
    # endpoint proves a queued turn exists.
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False

    # Turn 2's queued endpoint redeems the credit: the turn activates,
    # prepares, and seals so the final right behind it can deliver.
    await runtime._handle_independent_asr_endpoint(epoch)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    await runtime._handle_independent_asr_final("second", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second"]
    assert runtime.handle_new_message.await_count == 2
    assert runtime._asr_overlap_completed_turns == 0


async def test_two_completed_overlaps_replay_in_order_after_delayed_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # Turns 2 and 3 each start and reach local silence while turn 1 is still
    # ACTIVE: one completed-overlap credit accumulates per onset+pause cycle.
    for _ in range(2):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_RESUMED,
            epoch,
        )
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.CANDIDATE_PAUSE,
            epoch,
        )
    assert runtime._asr_overlap_completed_turns == 2

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    for text in ("second", "third"):
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(text, epoch, "openai")
        await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["first", "second", "third"]
    assert runtime.handle_new_message.await_count == 3
    assert runtime._asr_overlap_completed_turns == 0


async def test_hard_mute_clears_completed_overlap_credit() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "openai")
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = []
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_completed_turns == 1

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            12,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    # Hard mute tears the turn state down: neither the onset nor the credit
    # may survive to wake a replacement turn.
    assert runtime._asr_overlap_onset_token is None
    assert runtime._asr_overlap_completed_token is None
    assert runtime._asr_overlap_completed_turns == 0

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("ghost", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # Turn 1's preparation before the mute awaited handle_new_message once;
    # the muted ghost final must not deliver a transcript or a second turn.
    assert runtime.handle_input_transcript.await_count == 0
    assert runtime.handle_new_message.await_count == 1


async def test_stale_completed_overlap_is_dropped_at_next_endpoint() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE,
        epoch,
    )
    assert runtime._asr_overlap_completed_turns == 1

    # The audio generation moves on before the delayed final, so the credit
    # belongs to a stale ingress and must not wake a replacement turn.
    component = runtime._asr_runtime
    component._asr_audio_generation += 1
    component._asr_current_ingress_token = runtime._capture_ingress_token()

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    await runtime._handle_independent_asr_endpoint(epoch)
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False
    assert runtime._asr_overlap_completed_turns == 0
    await runtime._handle_independent_asr_final("ghost", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    # Both the overlap credit and the final belong to the superseded full
    # VoiceTurnToken once audio_generation rotates.
    runtime.handle_input_transcript.assert_not_awaited()
    assert runtime.handle_new_message.await_count == 1


async def test_smart_turn_active_resumed_is_not_recorded_for_replay() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    # SmartTurn authority orders activity and endpoint through one detector
    # queue, so a mid-turn resume is same-turn speech and must stay a no-op.
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    assert runtime._asr_turn_prepared is False
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["hello"]
    assert runtime.handle_new_message.await_count == 1


async def test_draining_pending_turn_overflow_discards_candidate_and_reports_backpressure() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_RESUMED,
        epoch,
    )

    await runtime._route_microphone_audio(
        b"\x01\x00" * (16_000 * 9),
        sample_rate_hz=16_000,
    )

    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_session is asr
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
    assert runtime._asr_sealed_turn_token is not None
    assert runtime._asr_lifecycle.pending_turn_bytes == 0
    assert runtime._asr_lifecycle.has_pending_turn is False
    runtime._asr_detector.reset.assert_awaited_once()
    assert any(
        "ASR_INGRESS_BACKPRESSURE" in call.args[0]
        for call in runtime.send_status.await_args_list
    )
    assert runtime._omni_mic_audio_bytes == 0


async def test_active_ingress_backpressure_releases_keyed_core_turn_without_blocking(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    current_pause_id: str | None = None

    async def prepare_external_voice_turn(*, turn_id: str) -> None:
        nonlocal current_pause_id
        current_pause_id = turn_id

    def abandon_external_voice_turn(turn_id: str | None = None) -> None:
        nonlocal current_pause_id
        if turn_id is not None and turn_id != current_pause_id:
            return
        current_pause_id = None

    runtime.session.prepare_external_voice_turn = AsyncMock(
        side_effect=prepare_external_voice_turn
    )
    runtime.session.abandon_external_voice_turn = MagicMock(
        side_effect=abandon_external_voice_turn
    )
    core_session = runtime.session
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    current_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = current_ingress
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)

    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    prepared_turn_id = lifecycle.snapshot.turn_id
    runtime.session.prepare_external_voice_turn.assert_awaited_once()
    external_turn_id = (
        runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    )
    assert current_pause_id == external_turn_id
    backpressure_status_started = asyncio.Event()
    release_backpressure_status = asyncio.Event()

    async def block_backpressure_status(payload: str) -> None:
        if json.loads(payload).get("code") == "ASR_INGRESS_BACKPRESSURE":
            backpressure_status_started.set()
            await release_backpressure_status.wait()

    runtime.send_status.side_effect = block_backpressure_status
    backpressure_task = asyncio.create_task(
        component._handle_audio_ingress_backpressure(current_ingress)
    )
    await asyncio.wait_for(backpressure_status_started.wait(), 1)

    next_turn = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=lifecycle.snapshot.turn_id,
    )
    assert next_turn.turn_id != prepared_turn_id
    assert await runtime._prepare_core_voice_turn(next_turn) is True
    assert runtime.session.prepare_external_voice_turn.await_count == 2
    next_external_turn_id = (
        runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    )
    assert next_external_turn_id != external_turn_id
    assert current_pause_id == next_external_turn_id

    release_backpressure_status.set()
    await asyncio.wait_for(backpressure_task, 1)

    assert runtime.session is core_session
    assert runtime._asr_route_mode == "independent"
    assert component._asr_lifecycle is lifecycle
    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_session is None
    assert component._asr_detector is detector
    assert component._asr_current_ingress_token is None
    sessions[0].close.assert_awaited_once_with()
    detector.reset.assert_awaited_once_with()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        external_turn_id
    )
    assert current_pause_id == next_external_turn_id
    assert all(
        "ASR_INDEPENDENT_FAILED" not in call.args[0]
        for call in runtime.send_status.await_args_list
    )


async def test_transport_only_close_enters_deep_sleep_without_closing_detector() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    detector = type("Detector", (), {"close": AsyncMock()})()
    runtime._asr_detector = detector

    await runtime._close_transport_only()

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    assert runtime._asr_detector is detector
    assert runtime._asr_route_mode == "independent"
    asr.close.assert_awaited_once_with()
    detector.close.assert_not_awaited()


async def test_initial_ready_transport_also_expires_from_local_listen() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"close": AsyncMock()})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    policy = replace(
        resolve_provider_policy("qwen", "manual"),
        warm_transport_ms=10,
    )
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    runtime._schedule_transport_warm_expiry(runtime._asr_session_epoch)
    # 同上：10ms TTL 落在 Windows 定时器分辨率的量级里，固定 sleep 赌不起；
    # 到期任务本身就是「已过期并关闭」的同步点。
    expiry = runtime._asr_warm_expiry_task
    assert expiry is not None
    await asyncio.wait_for(expiry, 5)

    assert runtime._asr_session is None
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    asr.close.assert_awaited_once_with()


async def test_deep_sleep_speech_reconnects_and_flushes_pending_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
    detector = _ReadyDetector()
    detector.feed = AsyncMock(
        return_value=DetectorFeedResult((SpeechActivityEvent.SPEECH_STARTED,), True)
    )
    runtime._asr_detector = detector
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    new_asr.connect = AsyncMock()
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection("qwen")

    await runtime._route_microphone_audio(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )
    assert runtime._asr_transport_task is not None
    await runtime._asr_transport_task

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )


@pytest.mark.parametrize("provider", ["glm", "gemini"])
async def test_smart_turn_fail_open_buffers_until_deep_sleep_transport_reconnects(
    provider: str,
) -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = provider
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy(provider, "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
    runtime._asr_lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
    runtime._asr_detector = _QueuedSmartTurnDetector()
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    new_asr.connect = AsyncMock()
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection(provider)
    pcm16 = b"\x03\x00" * 160

    await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_transport_task is not None
    await runtime._asr_transport_task

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        pcm16,
        sample_rate_hz=16_000,
    )
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert all(status.get("code") != "ASR_BLOCKED_ENDPOINTING" for status in statuses)


async def test_optimization_disabled_buffers_until_initial_transport_is_ready() -> None:
    runtime = _Runtime()
    runtime._voice_input_resource_optimization_enabled = False
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "glm"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
        shadow_mode=False,
        resource_optimization_enabled=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _QueuedSmartTurnDetector()
    new_asr = type("Asr", (), {})()
    new_asr.is_ready = True
    new_asr.connect = AsyncMock()
    new_asr.stream_audio = AsyncMock()
    runtime._asr_session_factory = MagicMock(return_value=new_asr)
    runtime._asr_transport_selection = _selection("glm")
    pcm16 = b"\x04\x00" * 160

    await runtime._route_microphone_audio(
        pcm16,
        sample_rate_hz=16_000,
        rnnoise_available=False,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_transport_task is not None
    await runtime._asr_transport_task

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert runtime._asr_route_mode == "independent"
    assert runtime._omni_mic_audio_bytes == 0
    new_asr.connect.assert_awaited_once_with()
    new_asr.stream_audio.assert_awaited_once_with(
        pcm16,
        sample_rate_hz=16_000,
    )
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert all(status.get("code") != "ASR_BLOCKED_ENDPOINTING" for status in statuses)


async def test_hard_mute_is_backend_authoritative_and_rejects_stale_lease_events() -> (
    None
):
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    detector = type("Detector", (), {})()
    detector.reset = AsyncMock()
    detector.feed = AsyncMock(return_value=DetectorFeedResult((), True))
    runtime._asr_detector = detector
    runtime._clear_audio_stream_queue = MagicMock()
    runtime.hot_swap_audio_cache = [b"old-pcm"]
    old_token = runtime._capture_ingress_token(runtime._asr_lifecycle)

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            12,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    asr.close.assert_awaited_once_with()
    runtime._clear_audio_stream_queue.assert_called_once_with("lease_sync")
    assert runtime.hot_swap_audio_cache == []
    assert runtime._ingress_token_matches(old_token) is False
    detector.reset.assert_awaited_once_with()
    detector.feed.assert_not_awaited()
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_lifecycle.pre_roll_bytes == 0

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            11,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )
    assert runtime._voice_input_suppressed is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            13,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_suppressed is False


async def test_hard_mute_during_detector_await_invalidates_inflight_pcm() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    asr.stream_audio = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("qwen", "manual"),
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

    feed_started = asyncio.Event()
    release_feed = asyncio.Event()

    class _BlockingDetector(_ReadyDetector):
        async def feed(self, _pcm16: bytes, **_kwargs) -> DetectorFeedResult:
            feed_started.set()
            await release_feed.wait()
            return DetectorFeedResult((), True)

    runtime._asr_detector = _BlockingDetector()
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        runtime._asr_session_epoch,
    )

    route_task = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00" * 160,
            sample_rate_hz=16_000,
        )
    )
    await asyncio.wait_for(feed_started.wait(), 1)
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=True,
        focus_suppressed=False,
    )
    release_feed.set()

    assert await route_task is True
    asr.stream_audio.assert_not_awaited()
    assert runtime._asr_audio_bytes == 0
    assert runtime._omni_mic_audio_bytes == 0


async def test_hard_mute_suppresses_stale_audio_dispatcher_failure() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_detector = _ReadyDetector()
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    turn_token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(lifecycle),
        turn_id=lifecycle.snapshot.turn_id,
    )

    assert await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="core",
        hard_muted=True,
        focus_suppressed=False,
    )
    runtime.send_status.reset_mock()
    await runtime._handle_asr_audio_dispatcher_failure(
        turn_token,
        RuntimeError("old provider write failed after hard mute"),
    )

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle is lifecycle
    runtime.send_status.assert_not_awaited()


async def test_game_takeover_suppresses_stale_detector_dispatcher_failure() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.close = AsyncMock()
    runtime._asr_session = asr
    _install_ready_lifecycle(runtime, "qwen")
    detector = _ReadyDetector()
    detector.detector_epoch = 1
    runtime._asr_detector = detector
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    ingress_token = runtime._capture_ingress_token(lifecycle)
    envelope = CoreDetectorEventEnvelope(
        event=DetectorRuntimeEvent(
            ingress=DetectorIngressIdentity(
                ingress_token=ingress_token,
                detector_epoch=detector.detector_epoch,
                sequence_no=1,
            ),
            candidate=DetectorCandidateKey(detector.detector_epoch, 1),
            kind="control_lane_failed",
        ),
        detector_ref=detector,
        lifecycle_ref=lifecycle,
        session_epoch=runtime._asr_session_epoch,
    )

    assert await runtime._handle_voice_input_control(
        "game_takeover",
        1,
    )
    runtime.send_status.reset_mock()
    await runtime._handle_asr_detector_dispatcher_failure(
        envelope,
        RuntimeError("old detector callback failed after game takeover"),
    )

    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_lifecycle is lifecycle
    assert lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
    runtime.send_status.assert_not_awaited()


async def test_new_websocket_connection_resets_mic_lease_generation_once() -> None:
    runtime = _Runtime()
    runtime._voice_lease_generation = 12

    assert runtime._begin_voice_input_connection("socket-a") is True
    assert runtime._voice_lease_generation == -1
    assert runtime._voice_lease_control_seen is False
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_lease_control_seen is True
    assert runtime._voice_input_accepts_pcm() is False

    assert runtime._begin_voice_input_connection("socket-a") is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )

    assert runtime._begin_voice_input_connection("socket-b") is True
    assert runtime._voice_lease_control_seen is False
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True


async def test_legacy_audio_session_authorization_is_one_shot() -> None:
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("legacy-socket") is True
    assert await runtime._ensure_voice_input_session_authorized("legacy-socket") is True
    assert runtime._voice_lease_generation == 0
    assert runtime._voice_lease_synchronized is True
    assert runtime._voice_lease_owner == "core"
    assert runtime._voice_lease_hard_muted is False
    assert runtime._voice_lease_focus_suppressed is False
    assert runtime._voice_input_accepts_pcm() is True
    runtime._asr_runtime.abort.assert_awaited_once_with("legacy_session_start")

    runtime._asr_runtime.abort.reset_mock()
    assert await runtime._ensure_voice_input_session_authorized("legacy-socket") is True
    assert runtime._voice_lease_generation == 0
    runtime._asr_runtime.abort.assert_not_awaited()


async def test_explicit_owner_none_cannot_be_overridden_by_legacy_authorization() -> (
    None
):
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("explicit-socket") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._asr_runtime.abort.reset_mock()

    assert (
        await runtime._ensure_voice_input_session_authorized("explicit-socket") is True
    )
    assert runtime._voice_lease_generation == 1
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    runtime._asr_runtime.abort.assert_not_awaited()


@pytest.mark.parametrize(
    ("event", "generation"),
    [
        ("invalid-control", 0),
        ("lease_sync", -1),
    ],
)
async def test_rejected_explicit_control_permanently_disables_legacy_fallback(
    event: str,
    generation: int,
) -> None:
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("explicit-socket") is True
    assert (
        await runtime._handle_voice_input_control(
            event,
            generation,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )
    assert runtime._voice_lease_control_seen is True
    assert (
        await runtime._ensure_voice_input_session_authorized("explicit-socket") is False
    )
    await runtime._enqueue_audio_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        }
    )

    assert runtime._voice_lease_synchronized is False
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._audio_stream_queue.empty()
    runtime._asr_runtime.abort.assert_not_awaited()


async def test_legacy_authorization_loses_race_to_new_connection_identity() -> None:
    runtime = _Runtime()
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def _block_old_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    old_abort = AsyncMock(side_effect=_block_old_abort)
    runtime._asr_runtime.abort = old_abort
    assert runtime._begin_voice_input_connection("socket-a") is True

    authorize_task = asyncio.create_task(
        runtime._ensure_voice_input_session_authorized("socket-a")
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    assert runtime._begin_voice_input_connection("socket-b") is True
    runtime._asr_runtime.abort = AsyncMock()
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    release_abort.set()

    assert await authorize_task is False
    assert runtime._voice_lease_connection_id == "socket-b"
    assert runtime._voice_lease_generation == 1
    assert runtime._voice_lease_control_seen is True
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    old_abort.assert_awaited_once_with("legacy_session_start")


async def test_game_owner_and_hard_mute_remain_simultaneously_authoritative() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_lease_owner == "game"
    assert runtime._voice_lease_hard_muted is True
    assert runtime._voice_input_suppression_reasons == {"game", "hard_mute"}
    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED


async def test_accepted_final_is_recorded_and_injected_once() -> None:
    runtime = _Runtime()
    runtime._asr_provider = "glm"
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")

    await asyncio.gather(
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
        runtime._handle_independent_asr_final(" hello ", epoch, "glm"),
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "glm"},
    )
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_final_records_segmented_wire_audio_committed_at_seal() -> None:
    runtime = _Runtime()
    session = SimpleNamespace(is_ready=True, provider_wire_audio_ms=0)
    runtime._asr_session = session
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "glm")
    # Segmented sessions advance the cumulative counter only at the seal-time
    # physical-segment commit, after the dispatcher's last per-chunk sample.
    session.provider_wire_audio_ms = 480

    await runtime._handle_independent_asr_final("hello", epoch, "glm")
    await runtime._wait_asr_transcript_dispatch_idle()

    metrics = runtime._asr_lifecycle.metrics
    assert metrics.provider_wire_audio_ms == 480
    assert metrics.cloud_audio_ms == 480
    assert runtime._asr_last_provider_wire_audio_ms == 480


async def test_final_does_not_double_count_sampled_streaming_wire_audio() -> None:
    runtime = _Runtime()
    session = SimpleNamespace(is_ready=True, provider_wire_audio_ms=480)
    runtime._asr_session = session
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    # Streaming sessions advance the counter inside stream_audio, so the
    # per-chunk dispatcher sample has already recorded the full amount.
    runtime._sync_provider_wire_metrics(session)
    assert runtime._asr_lifecycle.metrics.provider_wire_audio_ms == 480

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    metrics = runtime._asr_lifecycle.metrics
    assert metrics.provider_wire_audio_ms == 480
    assert metrics.cloud_audio_ms == 480
    assert runtime._asr_last_provider_wire_audio_ms == 480


async def test_identical_text_in_consecutive_turns_is_delivered_twice() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch

    for _ in range(2):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final("嗯", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == ["嗯", "嗯"]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "嗯",
        "嗯",
    ]


async def test_blocked_core_response_does_not_block_next_asr_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    response_started = asyncio.Event()
    release_response = asyncio.Event()

    async def block_response(_text: str) -> None:
        response_started.set()
        await release_response.wait()

    runtime.session.create_response.side_effect = block_response
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first", epoch, "qwen")
    await response_started.wait()

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )

    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    release_response.set()
    await runtime._wait_asr_transcript_dispatch_idle()


async def test_core_swap_cancels_blocked_old_final_without_touching_new_state() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    old_epoch = runtime._asr_session_epoch
    old_core_session = runtime.session
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()

    async def block_transcript(_text: str, **_kwargs: object) -> bool:
        transcript_started.set()
        await release_transcript.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_transcript
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        old_epoch,
    )
    await runtime._handle_independent_asr_endpoint(old_epoch)
    await runtime._handle_independent_asr_final("old", old_epoch, "qwen")
    await transcript_started.wait()

    await runtime._close_independent_asr(next_route_mode="blocked")
    new_core_session = type("NewCore", (), {})()
    new_core_session.create_response = AsyncMock()
    new_core_session.handle_interruption = AsyncMock()
    runtime.session = new_core_session
    _install_ready_lifecycle(runtime, "qwen")
    new_lifecycle = runtime._asr_lifecycle
    assert new_lifecycle is not None
    expected_state = new_lifecycle.snapshot.state

    release_transcript.set()
    await asyncio.sleep(0)

    old_core_session.create_response.assert_not_awaited()
    new_core_session.create_response.assert_not_awaited()
    assert runtime._asr_lifecycle is new_lifecycle
    assert new_lifecycle.snapshot.state is expected_state
    assert runtime._asr_sealed_turn_token is None


async def test_late_first_final_then_second_final_recovers_in_linear_order() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch
    events: list[str] = []

    runtime.session.handle_interruption.side_effect = lambda: events.append(
        "interruption"
    )
    runtime.handle_new_message.side_effect = lambda: events.append("prepare")
    runtime.handle_input_transcript.side_effect = lambda text, **_kwargs: (
        events.append(f"transcript:{text}") or True
    )
    runtime.session.create_response.side_effect = lambda text: events.append(
        f"response:{text}"
    )

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("first fragment", epoch, "openai")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    await runtime._handle_independent_asr_final("second fragment", epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert events.count("interruption") == 2
    assert events.count("prepare") == 2
    assert [event for event in events if event.startswith("transcript:")] == [
        "transcript:first fragment",
        "transcript:second fragment",
    ]
    assert [event for event in events if event.startswith("response:")] == [
        "response:first fragment",
        "response:second fragment",
    ]


async def test_three_pending_finals_recover_without_request_multiplication() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    epoch = runtime._asr_session_epoch

    for text in ("first", "second", "third"):
        await runtime._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            epoch,
        )
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(text, epoch, "openai")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert runtime.session.handle_interruption.await_count == 3
    assert runtime.handle_new_message.await_count == 3
    assert [
        call.args[0] for call in runtime.handle_input_transcript.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]
    assert [
        call.args[0] for call in runtime.session.create_response.await_args_list
    ] == [
        "first",
        "second",
        "third",
    ]


async def test_consumed_or_suppressed_final_does_not_create_response() -> None:
    runtime = _Runtime()
    runtime.handle_input_transcript.return_value = False
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "echo",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.session.create_response.assert_not_awaited()


async def test_close_invalidates_late_final_before_waiting_for_provider() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    old_epoch = runtime._asr_session_epoch

    await runtime._close_independent_asr(next_route_mode="blocked")
    await runtime._handle_independent_asr_final("late", old_epoch, "glm")

    asr.close.assert_awaited_once_with()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"


async def test_close_releases_independent_audio_pipeline() -> None:
    runtime = _Runtime()
    pipeline = type("Pipeline", (), {})()
    pipeline.close = AsyncMock()
    runtime._voice_input_audio_pipeline = pipeline

    await runtime._close_independent_asr(next_route_mode="blocked")

    pipeline.close.assert_awaited_once_with()
    assert runtime._voice_input_audio_pipeline is not pipeline


async def test_close_failure_keeps_the_requested_blocked_route() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock(side_effect=RuntimeError("close failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )


async def test_close_requires_callers_to_declare_the_next_route() -> None:
    parameter = inspect.signature(AsrRuntimeMixin._close_independent_asr).parameters[
        "next_route_mode"
    ]

    assert parameter.default is inspect.Parameter.empty


async def test_asr_stream_failure_never_replays_the_failed_frame_to_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(side_effect=RuntimeError("sensitive provider body"))
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()

    assert consumed is True
    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "sensitive provider body" not in str(runtime.send_status.await_args)


async def test_asr_backpressure_reports_specific_blocking_status() -> None:
    runtime = _Runtime()
    blocking_status_sent = asyncio.Event()

    async def record_status(message: str) -> None:
        if "ASR_STREAM_BACKPRESSURE" in message:
            blocking_status_sent.set()

    runtime.send_status.side_effect = record_status
    asr = type("Asr", (), {})()
    asr.is_ready = True
    asr.stream_audio = AsyncMock(
        side_effect=RuntimeError("ASR_STREAM_BACKPRESSURE: queue full")
    )
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_provider = "qwen"
    runtime._asr_route_mode = "independent"
    await _install_active_smart_turn(runtime, "qwen")

    await runtime._route_microphone_audio(
        b"\x00\x00" * 160,
        sample_rate_hz=16_000,
    )
    await runtime._asr_audio_dispatcher.wait_idle()
    await asyncio.wait_for(blocking_status_sent.wait(), 1)

    assert "ASR_STREAM_BACKPRESSURE" in runtime.send_status.await_args.args[0]
    assert runtime._asr_route_mode == "blocked"


async def test_independent_asr_setting_is_persisted_as_a_boolean() -> None:
    assert "independentAsrEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS
    assert (
        "voiceInputResourceOptimizationEnabled"
        in preferences._ALLOWED_CONVERSATION_SETTINGS
    )
    assert (
        "voice_input_resource_optimization_enabled"
        not in preferences._ALLOWED_CONVERSATION_SETTINGS
    )


async def test_start_uses_current_core_route_only_after_provider_ready(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"
    assert factory.call_args.args == ("gemini",)
    assert factory.call_args.kwargs["selection"].provider_key == "gemini"


async def test_runtime_builds_primary_candidate_from_its_single_selection(
    monkeypatch,
) -> None:
    import main_logic.asr_client as asr_client
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    selection = asr_client._AsrSelection(
        provider_key="gemini",
        endpointing_mode="manual",
    )
    resolver = MagicMock(return_value=selection)
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    builder = MagicMock(return_value=asr)

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        builder,
        raising=False,
    )
    assert not hasattr(runtime_module, "create_asr_session")

    await runtime._start_independent_asr_if_enabled("audio")

    resolver.assert_called_once_with("gemini")
    assert builder.call_args.kwargs["selection"] is selection
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"


async def _start_bridge_and_capture_builder_call(monkeypatch, runtime):
    import main_logic.asr_client.runtime as runtime_module

    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    builder = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        builder,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "independent"
    return builder.call_args.kwargs


async def test_start_forwards_core_user_language_to_session_builder(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.user_language = "ja"

    kwargs = await _start_bridge_and_capture_builder_call(monkeypatch, runtime)

    assert kwargs["user_language"] == "ja"


async def test_start_without_user_language_builds_session_without_hint(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    assert getattr(runtime, "user_language", None) is None

    kwargs = await _start_bridge_and_capture_builder_call(monkeypatch, runtime)

    assert kwargs["user_language"] is None


async def test_startup_close_window_is_blocked_before_settings_resolution(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    class _OldAsr:
        is_ready = True

        async def close(self) -> None:
            close_started.set()
            await release_close.wait()

    runtime._asr_session = _OldAsr()
    runtime._asr_route_mode = "independent"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    start_task = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(close_started.wait(), 1)

    assert runtime._asr_route_mode == "blocked"
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )

    release_close.set()
    await asyncio.wait_for(start_task, 1)
    assert runtime._asr_route_mode == "native"
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_explicit_intl_soniox_is_selected_before_audio(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("soniox", "provider")),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_received_audio is False


async def test_soniox_connect_failure_retries_same_selection_before_audio(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    soniox_selection = _selection("soniox", "provider")
    primary_resolver = MagicMock(return_value=soniox_selection)
    forbidden_core_resolver = MagicMock(
        side_effect=AssertionError("Soniox recovery must not resolve another provider")
    )
    save_settings = MagicMock(
        side_effect=AssertionError("Provider recovery must not rewrite user settings")
    )
    sleep = AsyncMock()
    sessions = []
    for side_effect in (
        RuntimeError("provider detail 1"),
        RuntimeError("provider detail 2"),
        None,
    ):
        session = type("Soniox", (), {})()
        session.connect = AsyncMock(side_effect=side_effect)
        session.close = AsyncMock()
        sessions.append(session)
    built_selections = []
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        primary_resolver,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        forbidden_core_resolver,
        raising=False,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(
        preferences,
        "save_global_conversation_settings",
        save_settings,
    )

    def build_candidate(_core_type, *, selection, **_kwargs):
        assert runtime._asr_provider == "soniox"
        built_selections.append(selection)
        assert selection is soniox_selection
        return sessions[len(built_selections) - 1]

    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        build_candidate,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    sessions[0].close.assert_awaited_once_with()
    sessions[1].close.assert_awaited_once_with()
    sessions[2].close.assert_not_awaited()
    for session in sessions:
        session.connect.assert_awaited_once_with()
    primary_resolver.assert_called_once_with("gemini")
    forbidden_core_resolver.assert_not_called()
    save_settings.assert_not_called()
    assert built_selections == [soniox_selection] * 3
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    assert runtime._asr_session is sessions[2]
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_transport_selection is soniox_selection
    assert runtime._asr_lifecycle.provider_policy.endpoint_authority == "provider"
    assert runtime._asr_route_mode == "independent"
    assert "provider detail" not in str(runtime.send_status.await_args_list)
    assert "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE" not in str(
        runtime.send_status.await_args_list
    )


async def test_soniox_connect_retries_exhausted_blocks_without_provider_fallback(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.stream_audio = AsyncMock()
    soniox_selection = _selection("soniox", "provider")
    forbidden_core_resolver = MagicMock(
        side_effect=AssertionError("Soniox recovery must not resolve another provider")
    )
    sleep = AsyncMock()
    sessions = []
    for attempt in range(3):
        session = type("Soniox", (), {})()
        session.connect = AsyncMock(
            side_effect=RuntimeError(f"private provider detail {attempt}")
        )
        session.close = AsyncMock()
        sessions.append(session)
    built_selections = []

    def create_candidate(_core_type, *, selection, **_kwargs):
        built_selections.append(selection)
        assert selection is soniox_selection
        return sessions[len(built_selections) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=soniox_selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        forbidden_core_resolver,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)

    await runtime._start_independent_asr_if_enabled("audio")

    consumed = await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    )
    for session in sessions:
        session.connect.assert_awaited_once_with()
        session.close.assert_awaited_once_with()
    forbidden_core_resolver.assert_not_called()
    assert built_selections == [soniox_selection] * 3
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    assert runtime._asr_session is None
    assert runtime._asr_provider is None
    assert runtime._asr_route_mode == "blocked"
    assert consumed is True
    runtime.session.stream_audio.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses[-1] == {
        "code": "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE",
        "details": {
            "provider": "soniox",
            "session_epoch": runtime._asr_session_epoch,
        },
    }
    assert "private provider detail" not in str(runtime.send_status.await_args_list)


async def test_failed_soniox_candidate_cannot_invalidate_successful_successor(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.websocket = type("WebSocket", (), {"send_json": AsyncMock()})()
    callbacks: list[dict[str, object]] = []

    failed_session = type("Soniox", (), {})()
    failed_session.connect = AsyncMock(side_effect=RuntimeError("provider detail"))
    failed_session.close = AsyncMock()
    successful_session = type("Soniox", (), {})()
    successful_session.connect = AsyncMock()
    successful_session.close = AsyncMock()
    soniox_selection = _selection("soniox", "provider")
    sessions = [failed_session, successful_session]

    def capture_partial(session, callback) -> None:
        session.partial_callback = callback

    def create_candidate(_core_type, *, selection, **kwargs):
        assert selection is soniox_selection
        callbacks.append(kwargs)
        return sessions[len(callbacks) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=soniox_selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        MagicMock(
            side_effect=AssertionError(
                "Soniox recovery must not resolve another provider"
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(runtime_module, "_attach_partial_callback", capture_partial)
    monkeypatch.setattr(runtime_module.asyncio, "sleep", AsyncMock())

    await runtime._start_independent_asr_if_enabled("audio")
    adopted_epoch = runtime._asr_session_epoch

    await callbacks[0]["on_input_transcript"]("late soniox final")
    await callbacks[0]["on_speech_activity"](SpeechActivityEvent.SPEECH_STARTED)
    await failed_session.partial_callback("late soniox preview")
    await callbacks[0]["on_connection_error"]("late soniox error")
    await asyncio.sleep(0)

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.handle_interruption.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    runtime.websocket.send_json.assert_not_awaited()
    successful_session.close.assert_not_awaited()
    assert runtime._asr_session is successful_session
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_session_epoch == adopted_epoch


async def test_adopted_start_activity_callback_survives_idle_audio_generation_bump(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    original_audio_generation = component._asr_audio_generation
    current_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = current_ingress

    await component._handle_audio_ingress_backpressure(current_ingress)

    assert component._asr_audio_generation == original_audio_generation + 1
    assert component._asr_session is sessions[0]
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    detector.reset.assert_awaited_once_with()

    updated_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = updated_ingress
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_current_ingress_token == updated_ingress
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sessions[0].close.assert_not_awaited()


async def test_reconnected_start_callback_survives_abort_start_generation_change(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, _detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    original_start_generation = component._asr_start_generation

    await component.abort("hard_mute")

    assert component._asr_start_generation > original_start_generation
    assert component._asr_session is None
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    sessions[0].close.assert_awaited_once_with()

    await component._restart_transport(max_attempts=1)

    assert len(callbacks) == 2
    assert component._asr_session is sessions[1]
    updated_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = updated_ingress
    old_activity = callbacks[0]["on_speech_activity"]
    new_activity = callbacks[1]["on_speech_activity"]
    assert callable(old_activity)
    assert callable(new_activity)

    await old_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN

    await new_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_current_ingress_token == updated_ingress
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sessions[1].close.assert_not_awaited()


async def test_restart_closes_not_ready_session_before_replacement() -> None:
    runtime = _Runtime()
    events: list[str] = []

    async def close_old() -> None:
        events.append("old.close")

    async def connect_new() -> None:
        events.append("new.connect")

    old_session = SimpleNamespace(
        is_ready=False,
        close=AsyncMock(side_effect=close_old),
    )
    candidate = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(side_effect=connect_new),
        close=AsyncMock(),
    )
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_session_factory = MagicMock(return_value=candidate)
    runtime._asr_transport_selection = _selection("qwen")

    await runtime._restart_transport(max_attempts=1)

    assert events == ["old.close", "new.connect"]
    old_session.close.assert_awaited_once_with()
    candidate.connect.assert_awaited_once_with()
    candidate.close.assert_not_awaited()
    assert runtime._asr_session is candidate


def _install_failing_restart_candidates(
    runtime: _Runtime,
    provider: str,
    *,
    failure_count: int,
) -> list[SimpleNamespace]:
    runtime._asr_session = SimpleNamespace(is_ready=False, close=AsyncMock())
    _install_ready_lifecycle(runtime, provider)
    candidates: list[SimpleNamespace] = []

    def build_candidate(_selection):
        candidate = SimpleNamespace(
            is_ready=True,
            connect=AsyncMock(
                side_effect=RuntimeError("private restart connect detail")
            ),
            close=AsyncMock(),
        )
        candidates.append(candidate)
        assert len(candidates) <= failure_count
        return candidate

    runtime._asr_session_factory = MagicMock(side_effect=build_candidate)
    runtime._asr_transport_selection = _selection(provider)
    return candidates


async def test_restart_default_attempts_follow_single_attempt_policy(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(runtime, "qwen", failure_count=1)
    assert runtime._asr_lifecycle.provider_policy.connect_max_attempts == 1

    await runtime._restart_transport()
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 1
    candidates[0].connect.assert_awaited_once_with()
    candidates[0].close.assert_awaited_once_with()
    sleep.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses[-1]["code"] == "ASR_INDEPENDENT_FAILED"
    assert "private restart connect detail" not in str(
        runtime.send_status.await_args_list
    )


async def test_restart_default_attempts_follow_soniox_policy_ladder(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(
        runtime, "soniox", failure_count=3
    )
    assert runtime._asr_lifecycle.provider_policy.connect_max_attempts == 3

    await runtime._restart_transport()
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 3
    for candidate in candidates:
        candidate.connect.assert_awaited_once_with()
        candidate.close.assert_awaited_once_with()
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses[-1]["code"] == "ASR_INDEPENDENT_FAILED"


async def test_restart_explicit_attempt_override_beats_policy(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(
        runtime, "soniox", failure_count=1
    )

    await runtime._restart_transport(max_attempts=1)
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 1
    candidates[0].connect.assert_awaited_once_with()
    sleep.assert_not_awaited()


async def test_restart_rejects_non_positive_attempt_override() -> None:
    runtime = _Runtime()

    with pytest.raises(ValueError, match="max_attempts must be positive"):
        await runtime._restart_transport(max_attempts=0)


async def test_not_ready_close_cannot_overwrite_replacement_generation() -> None:
    runtime = _Runtime()
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_old() -> None:
        close_started.set()
        await release_close.wait()

    old_session = SimpleNamespace(
        is_ready=False,
        close=AsyncMock(side_effect=close_old),
    )
    old_factory = MagicMock()
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_session_factory = old_factory
    runtime._asr_transport_selection = _selection("qwen")

    restarting = asyncio.create_task(runtime._restart_transport(max_attempts=1))
    await asyncio.wait_for(close_started.wait(), 1)
    assert runtime._asr_session is None

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    release_close.set()
    await asyncio.wait_for(restarting, 1)

    old_session.close.assert_awaited_once_with()
    old_factory.assert_not_called()
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    new_session.close.assert_not_awaited()


async def test_adopted_restart_cancellation_fails_closed_and_propagates(
    monkeypatch,
) -> None:
    runtime, sessions, _callbacks, detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    started_epoch = component._asr_session_epoch
    on_failure = AsyncMock(side_effect=component._callbacks.on_failure)
    component._callbacks = replace(component._callbacks, on_failure=on_failure)

    await component._close_transport_only()
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    component._asr_pending_speech_confirmed = True
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    prepare_started = asyncio.Event()
    keep_preparing = asyncio.Event()

    async def block_prepare(_epoch: int) -> None:
        prepare_started.set()
        await keep_preparing.wait()

    component._prepare_independent_asr_turn = AsyncMock(side_effect=block_prepare)
    restarting = asyncio.create_task(component._restart_transport(max_attempts=3))
    await asyncio.wait_for(prepare_started.wait(), 1)
    assert component._asr_session is sessions[1]

    restarting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restarting
    while component._asr_close_tasks:
        await asyncio.gather(
            *tuple(component._asr_close_tasks),
            return_exceptions=True,
        )

    on_failure.assert_awaited_once()
    failure = on_failure.await_args.args[0]
    assert failure.code == "ASR_INDEPENDENT_FAILED"
    assert failure.session_epoch == started_epoch + 1
    sessions[1].close.assert_awaited_once_with()
    detector.close.assert_awaited_once_with()
    assert component._asr_session is None
    assert component._asr_lifecycle is None
    assert component._asr_detector is None
    assert component._asr_session_factory is None
    assert component._asr_transport_selection is None
    assert component._asr_session_epoch == started_epoch + 1
    assert runtime._asr_route_mode == "blocked"
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert {
        "code": "ASR_INDEPENDENT_FAILED",
        "details": {
            "provider": "qwen",
            "session_epoch": started_epoch + 1,
        },
    } in statuses


async def test_adopted_restart_exception_fails_closed_without_retry(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(monkeypatch)
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    started_epoch = component._asr_session_epoch

    await component._close_transport_only()

    assert lifecycle.snapshot.state is VoiceLifecycleState.DEEP_SLEEP
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    assert lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
    component._asr_pending_speech_confirmed = True
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    component._prepare_independent_asr_turn = AsyncMock(
        side_effect=RuntimeError("post-adoption recovery failed")
    )

    await component._restart_transport(max_attempts=3)
    while component._asr_close_tasks:
        await asyncio.gather(
            *tuple(component._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(callbacks) == 2
    sessions[0].close.assert_awaited_once_with()
    sessions[1].connect.assert_awaited_once_with()
    sessions[1].close.assert_awaited_once_with()
    component._prepare_independent_asr_turn.assert_awaited_once_with(started_epoch)
    detector.close.assert_awaited_once_with()
    assert component._asr_session is None
    assert component._asr_lifecycle is None
    assert component._asr_detector is None
    assert component._asr_session_factory is None
    assert component._asr_transport_selection is None
    assert component._asr_session_epoch == started_epoch + 1
    assert runtime._asr_route_mode == "blocked"
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert {
        "code": "ASR_INDEPENDENT_FAILED",
        "details": {
            "provider": "qwen",
            "session_epoch": started_epoch + 1,
        },
    } in statuses


async def test_selection_failure_is_reported_without_escaping_session_start(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(side_effect=ValueError("invalid provider configuration")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_required")
    assert runtime._asr_session is None
    assert runtime._asr_provider is None
    assert "ASR_INDEPENDENT_FAILED" in runtime.send_status.await_args.args[0]
    assert "invalid provider configuration" not in str(
        runtime.send_status.await_args_list
    )


async def test_selection_failure_during_core_change_stays_blocked(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._independent_asr_route_key = "openai"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(side_effect=ValueError("invalid region configuration")),
    )

    await runtime._reconcile_independent_asr_after_core_change()

    assert runtime._independent_asr_route_key == "gemini"
    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "ASR_INDEPENDENT_FAILED" in runtime.send_status.await_args.args[0]


@pytest.mark.parametrize("core_type", ["qwen", "qwen_intl"])
async def test_qwen_core_starts_independent_asr_with_external_turn_support(
    monkeypatch,
    core_type: str,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = core_type
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("qwen")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    factory.assert_called_once()
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "qwen"


async def test_websocket_core_submits_one_external_turn_after_local_history() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime.session.submit_external_voice_turn = AsyncMock()
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_independent_asr_final(" hello ", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "qwen"},
    )
    runtime.session.submit_external_voice_turn.assert_awaited_once()
    call = runtime.session.submit_external_voice_turn.await_args
    assert call.args == ("hello",)
    assert call.kwargs["turn_id"].startswith("asr-")
    runtime.session.create_response.assert_not_awaited()


async def test_partial_preview_is_display_only_and_epoch_guarded() -> None:
    runtime = _Runtime()
    websocket = type("WebSocket", (), {})()
    websocket.send_json = AsyncMock()
    runtime.websocket = websocket
    runtime.current_speech_id = "speech-current"
    runtime._set_microphone_route("independent")
    await _install_active_smart_turn(runtime)
    epoch = runtime._asr_session_epoch
    token = runtime._asr_runtime._asr_partial_turn_token
    assert token is not None
    assert runtime._activate_asr_audio_dispatcher(runtime._asr_lifecycle, token)

    await runtime._send_independent_asr_preview(" draft ", epoch)
    await runtime._send_independent_asr_preview("stale", epoch + 1)

    websocket.send_json.assert_awaited_once_with(
        {
            "type": "user_transcript_preview",
            "text": "draft",
            "turn_id": "speech-current",
            "asr_turn_id": f"asr-{epoch}-1",
        }
    )
    runtime.handle_input_transcript.assert_not_awaited()


async def test_partial_preview_keeps_prepared_token_and_rejects_after_abort() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    on_partial = AsyncMock()
    runtime._asr_runtime._callbacks = replace(
        runtime._asr_runtime._callbacks,
        on_partial=on_partial,
    )
    await _install_active_smart_turn(runtime)
    epoch = runtime._asr_session_epoch
    captured_token = runtime._asr_runtime._asr_partial_turn_token
    assert captured_token is not None
    assert runtime._activate_asr_audio_dispatcher(
        runtime._asr_lifecycle,
        captured_token,
    )

    await runtime._send_independent_asr_preview("current", epoch)

    event = on_partial.await_args.args[0]
    assert event.turn_token is captured_token
    assert event.session_epoch == epoch
    on_partial.reset_mock()

    runtime._asr_audio_dispatcher.abort(captured_token)
    await runtime._send_independent_asr_preview("late", epoch)

    on_partial.assert_not_awaited()


async def test_start_failure_blocks_omni_without_leaking_error(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "glm"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock(side_effect=RuntimeError("secret provider response"))
    asr.close = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("glm")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=asr),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert "secret provider response" not in str(runtime.send_status.await_args)


async def test_builder_failure_stays_blocked_and_never_sends_audio_to_omni(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(side_effect=RuntimeError("private provider detail")),
    )

    await runtime._start_independent_asr_if_enabled("audio")
    consumed = await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    )
    if not consumed:
        await runtime.session.stream_audio(b"\x00\x00")

    assert runtime._asr_route_mode == "blocked"
    assert runtime._asr_session is None
    assert consumed is True
    runtime.session.stream_audio.assert_not_awaited()
    assert "private provider detail" not in str(runtime.send_status.await_args)


async def test_hot_swap_reuses_matching_asr_provider() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_hot_swap_replaces_asr_before_cached_audio_for_new_core() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "gemini"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with(
        "audio",
        preserve_hot_swap_audio=True,
    )


@pytest.mark.parametrize("core_type", ["openai", "glm", "gemini"])
async def test_hot_swap_starts_independent_asr_after_core_route_change(
    core_type: str,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = core_type
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "free"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_awaited_once_with(
        "audio",
        preserve_hot_swap_audio=True,
    )


async def test_hot_swap_does_not_retry_failed_same_core_route() -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    runtime._asr_route_mode = "blocked"
    runtime._independent_asr_route_key = "gemini"
    runtime._start_independent_asr_if_enabled = AsyncMock()

    await runtime._reconcile_independent_asr_after_core_change()

    runtime._start_independent_asr_if_enabled.assert_not_awaited()


async def test_disabled_native_route_key_prevents_same_core_reconcile(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert runtime._independent_asr_route_key == "gemini"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    old_token = runtime._capture_ingress_token()
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=old_token,
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    runtime._set_microphone_route("blocked")
    runtime._set_microphone_route("native")
    runtime._start_independent_asr_if_enabled = AsyncMock()
    await runtime._reconcile_independent_asr_after_core_change()
    runtime._start_independent_asr_if_enabled.assert_not_awaited()
    await runtime._flush_hot_swap_audio_cache()
    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)
    assert runtime._omni_mic_audio_bytes == 320


async def test_start_session_handshake_true_overrides_persisted_disabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_independent_asr_handshake(True)
    await runtime._start_independent_asr_if_enabled("audio")

    # The handshake beats the stale persisted value: the independent runtime
    # start is attempted instead of the native fallback.
    start_mock.assert_awaited_once()
    assert runtime._asr_route_mode != "native"


async def test_start_session_handshake_false_overrides_persisted_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_independent_asr_handshake(False)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


async def test_resource_optimization_handshake_false_overrides_persisted_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": True,
            }
        ),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_voice_input_resource_optimization_handshake(False)
    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["resource_optimization_enabled"] is False


async def test_provider_restart_reuses_accepted_session_optimization(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": True,
            }
        ),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.READY,
            provider="qwen",
            session_epoch=0,
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled(
        "audio",
        resource_optimization_override=False,
    )
    assert runtime._voice_input_resource_optimization_session_value is False

    # A losing/deduplicated request may overwrite the shared handshake, but a
    # provider-changing restart still belongs to the already accepted session.
    runtime.set_voice_input_resource_optimization_handshake(True)
    runtime.core_api_type = "openai"
    await runtime._reconcile_independent_asr_after_core_change()

    assert start_mock.await_count == 2
    assert all(
        call.kwargs["resource_optimization_enabled"] is False
        for call in start_mock.await_args_list
    )


@pytest.mark.parametrize("malformed", ["false", 0, 1, [False], {"enabled": False}])
async def test_resource_optimization_handshake_malformed_falls_back_to_persisted(
    monkeypatch,
    malformed,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": True,
            }
        ),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_voice_input_resource_optimization_handshake(malformed)
    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["resource_optimization_enabled"] is True


async def test_start_session_handshake_missing_falls_back_to_persisted(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    # An absent field (forwarded as None by the router) clears any override a
    # previous session left behind, restoring the persisted-setting behavior.
    runtime.set_independent_asr_handshake(True)
    runtime.set_independent_asr_handshake(None)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_awaited_once()


async def test_missing_independent_asr_setting_defaults_enabled(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_awaited_once()
    assert start_mock.await_args.kwargs["resource_optimization_enabled"] is True
    assert runtime._asr_route_mode != "native"


@pytest.mark.parametrize("malformed", ["true", 1, 0, [True], {"enabled": True}])
async def test_start_session_handshake_malformed_value_is_ignored(
    monkeypatch,
    malformed,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    # Strict bool typing: truthy non-bool values never enable the route.
    runtime.set_independent_asr_handshake(malformed)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


class _HotSwapRuntimeStub:
    def __init__(self, *, start_status: AsrStartStatus) -> None:
        self.session_epoch = 1
        self.audio_generation = 1
        self.active_provider: str | None = "provider-a"
        self.start_status = start_status
        self.submissions: list[tuple[str | None, bytes, object]] = []
        self.abort = AsyncMock()

    def capture_ingress_token(
        self,
        *,
        connection_id: str,
        lease_generation: int,
        route_generation: int,
    ):
        from main_logic.voice_turn.contracts import VoiceIngressToken

        return VoiceIngressToken(
            self.session_epoch,
            connection_id,
            lease_generation,
            route_generation,
            self.audio_generation,
        )

    async def close(self) -> None:
        self.session_epoch += 1
        self.audio_generation += 1
        self.active_provider = None

    async def start(
        self,
        *,
        route_key: str,
        resource_optimization_enabled: bool,
        user_language: str | None = None,
    ) -> AsrStartResult:
        _ = (route_key, resource_optimization_enabled, user_language)
        self.active_provider = (
            "provider-b" if self.start_status is AsrStartStatus.READY else None
        )
        return AsrStartResult(
            self.start_status,
            provider="provider-b",
            session_epoch=self.session_epoch,
        )

    async def submit(self, frame, *, ingress_token) -> AsrSubmitResult:
        self.submissions.append((self.active_provider, frame.pcm16, ingress_token))
        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)


async def test_provider_hot_swap_drops_cached_pcm_from_old_asr_generation(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    bridge = _HotSwapRuntimeStub(start_status=AsrStartStatus.READY)
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    runtime._independent_asr_route_key = "gemini"
    old_token = runtime._capture_ingress_token()
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=old_token,
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._reconcile_independent_asr_after_core_change()

    assert len(runtime.hot_swap_audio_cache) == 1
    assert runtime._asr_route_mode == "independent"
    await runtime._flush_hot_swap_audio_cache()

    assert bridge.submissions == []
    runtime.session.stream_audio.assert_not_awaited()


async def test_failed_provider_hot_swap_blocks_and_discards_cached_pcm(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    bridge = _HotSwapRuntimeStub(start_status=AsrStartStatus.UNAVAILABLE)
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "glm"
    runtime.input_mode = "audio"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    runtime._independent_asr_route_key = "gemini"
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=runtime._capture_ingress_token(),
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._reconcile_independent_asr_after_core_change()
    assert runtime._asr_route_mode == "blocked"
    await runtime._flush_hot_swap_audio_cache()

    assert bridge.submissions == []
    assert not runtime.hot_swap_audio_cache
    runtime.session.stream_audio.assert_not_awaited()


async def test_current_audio_pipeline_failure_blocks_once_without_pcm() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = False
    runtime.is_flushing_hot_swap_cache = False
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    runtime._asr_runtime.abort = AsyncMock()
    runtime._asr_runtime.submit = AsyncMock()
    runtime._voice_input_audio_pipeline.process = AsyncMock(
        side_effect=RuntimeError("soxr failed")
    )
    token = runtime._capture_ingress_token()
    message = {
        "input_type": "audio",
        "sample_rate_hz": 48_000,
        "data": [1] * 480,
    }

    await runtime._process_microphone_stream_data(
        message,
        ingress_token=token,
    )
    await runtime._process_microphone_stream_data(
        message,
        ingress_token=token,
    )

    assert runtime._asr_route_mode == "blocked"
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")
    runtime._asr_runtime.submit.assert_not_awaited()
    runtime.session.stream_audio.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert statuses == [
        {
            "code": "ASR_AUDIO_PREPROCESSING_FAILED",
            "details": {"provider": "glm", "session_epoch": 0},
        }
    ]


async def test_pipeline_failure_from_replaced_connection_is_silent() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def delayed_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=delayed_abort)
    token = runtime._capture_ingress_token()
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=token,
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    assert runtime._begin_voice_input_connection("replacement-connection")
    replacement_lease_state = (
        runtime._voice_lease_connection_id,
        runtime._voice_lease_generation,
        runtime._voice_lease_owner,
        runtime._voice_lease_synchronized,
    )
    release_abort.set()
    await asyncio.wait_for(failure, 1)

    assert (
        runtime._voice_lease_connection_id,
        runtime._voice_lease_generation,
        runtime._voice_lease_owner,
        runtime._voice_lease_synchronized,
    ) == replacement_lease_state
    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


@pytest.mark.parametrize(
    "changed_identity",
    [
        "lease_generation",
        "hard_mute",
        "focus_suppression",
        "game_takeover",
        "route_operation",
        "pipeline",
        "core_session",
    ],
)
async def test_stale_pipeline_failure_never_reports_to_current_identity(
    changed_identity: str,
) -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def delayed_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=delayed_abort)
    token = runtime._capture_ingress_token()
    source_pipeline = runtime._voice_input_audio_pipeline
    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=token,
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=source_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    if changed_identity == "lease_generation":
        runtime._voice_lease_generation += 1
    elif changed_identity == "hard_mute":
        runtime._voice_lease_hard_muted = True
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "focus_suppression":
        runtime._voice_lease_focus_suppressed = True
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "game_takeover":
        runtime._voice_lease_owner = "game"
        runtime._voice_input_transition_generation += 1
    elif changed_identity == "route_operation":
        object.__setattr__(
            runtime,
            "_asr_route_operation_generation",
            runtime._asr_route_operation_generation + 1,
        )
    elif changed_identity == "pipeline":
        runtime._voice_input_audio_pipeline = SimpleNamespace(
            process=AsyncMock(),
            close=AsyncMock(),
        )
        runtime._voice_input_pipeline_failed = False
    elif changed_identity == "core_session":
        runtime.session = SimpleNamespace(stream_audio=AsyncMock())
    else:
        raise AssertionError(changed_identity)

    release_abort.set()
    await asyncio.wait_for(failure, 1)

    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


async def test_replaced_audio_pipeline_late_failure_is_silent() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.is_flushing_hot_swap_cache = False
    runtime.session.stream_audio = AsyncMock()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime.abort = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fail_late(*_args, **_kwargs):
        started.set()
        await release.wait()
        raise RuntimeError("old pipeline failed")

    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.process = AsyncMock(side_effect=fail_late)
    token = runtime._capture_ingress_token()
    processing = asyncio.create_task(
        runtime._process_microphone_stream_data(
            {
                "input_type": "audio",
                "sample_rate_hz": 48_000,
                "data": [1] * 480,
            },
            ingress_token=token,
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    runtime._voice_input_audio_pipeline = type(
        "ReplacementPipeline",
        (),
        {"process": AsyncMock(), "close": AsyncMock()},
    )()
    runtime._set_microphone_route("blocked")
    runtime._set_microphone_route("independent")
    release.set()
    await asyncio.wait_for(processing, 1)

    runtime._asr_runtime.abort.assert_not_awaited()
    runtime.session.stream_audio.assert_not_awaited()
    runtime.send_status.assert_not_awaited()
    assert runtime._voice_input_pipeline_failed is False


async def test_old_abort_release_cannot_close_replacement_session() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class BlockingLease:
        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    runtime._asr_smart_turn_lease = BlockingLease()
    abort_task = asyncio.create_task(runtime._asr_runtime.abort("test_abort"))
    await asyncio.wait_for(release_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_old_lease.set()
    await asyncio.wait_for(abort_task, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_lifecycle is not old_lifecycle
    assert runtime._asr_detector is not old_detector
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()


async def test_old_failure_callback_cannot_detach_replacement_runtime() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    blocked_started = asyncio.Event()
    release_blocked = asyncio.Event()

    async def block_old_lifecycle(payload: str) -> None:
        status = json.loads(payload)
        if (
            status.get("code") == "ASR_LIFECYCLE_STATE"
            and status.get("details", {}).get("state") == "blocked"
        ):
            blocked_started.set()
            await release_blocked.wait()

    runtime.send_status.side_effect = block_old_lifecycle
    old_epoch = runtime._asr_session_epoch
    failure_task = asyncio.create_task(
        runtime._handle_independent_asr_error(old_epoch, "qwen")
    )
    await asyncio.wait_for(blocked_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_blocked.set()
    await asyncio.wait_for(failure_task, 1)
    await asyncio.sleep(0)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert old_lifecycle.snapshot.state is VoiceLifecycleState.OFF
    old_detector.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    new_detector.close.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"


async def test_old_detector_endpoint_cannot_seal_replacement_runtime() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
    detector = _QueuedSmartTurnDetector()
    detector.detector_epoch = 1
    runtime._asr_detector = detector
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    turn_token = runtime._asr_runtime._capture_turn_token(lifecycle)
    detector._token = turn_token
    candidate = DetectorCandidateKey(detector.detector_epoch, 1)
    envelope = CoreDetectorEventEnvelope(
        event=DetectorTurnEvent(
            ingress=DetectorIngressIdentity(
                ingress_token=turn_token.ingress,
                detector_epoch=detector.detector_epoch,
                sequence_no=1,
            ),
            bound_turn=BoundDetectorTurn(
                candidate=candidate,
                turn_token=turn_token,
            ),
            kind="complete",
        ),
        detector_ref=detector,
        lifecycle_ref=lifecycle,
        session_epoch=runtime._asr_session_epoch,
    )
    draining_started = asyncio.Event()
    release_draining = asyncio.Event()

    async def block_old_lifecycle(payload: str) -> None:
        status = json.loads(payload)
        if (
            status.get("code") == "ASR_LIFECYCLE_STATE"
            and status.get("details", {}).get("state") == "draining"
        ):
            draining_started.set()
            await release_draining.wait()

    runtime.send_status.side_effect = block_old_lifecycle
    endpoint_task = asyncio.create_task(
        runtime._asr_runtime._dispatch_asr_detector_event(envelope)
    )
    await asyncio.wait_for(draining_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_draining.set()
    await asyncio.wait_for(endpoint_task, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    new_session.close.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"
    statuses = [
        json.loads(call.args[0]).get("code")
        for call in runtime.send_status.await_args_list
    ]
    assert "ASR_AUDIO_ORDERING_FAILED" not in statuses


async def test_old_smart_turn_release_cannot_clear_replacement_lease() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    detector = runtime._asr_detector
    assert lifecycle is not None
    assert detector is not None
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class BlockingLease:
        token = object()

        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    old_lease = BlockingLease()
    runtime._asr_smart_turn_lease = old_lease
    prepare_task = asyncio.create_task(
        runtime._asr_runtime._ensure_smart_turn_ready(
            lifecycle,
            runtime._asr_session_epoch,
        )
    )
    await asyncio.wait_for(release_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_lease = _TestSmartTurnLease(
        runtime._asr_runtime._capture_turn_token(new_lifecycle)
    )
    runtime._asr_smart_turn_lease = new_lease
    release_old_lease.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    assert runtime._asr_smart_turn_lease is new_lease
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert new_lease.released is False


async def test_stale_detector_feed_exception_cannot_fail_new_generation() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingDetector(_ReadyDetector):
        async def feed(self, _pcm16: bytes, **_kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("old detector failed")

    runtime._asr_detector = _BlockingDetector()
    ingress = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = ingress
    submit = asyncio.create_task(
        runtime._asr_runtime.submit(
            ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.8, True),
            ingress_token=ingress,
        )
    )
    await asyncio.wait_for(started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release.set()
    result = await asyncio.wait_for(submit, 1)

    assert result.status is AsrSubmitStatus.STALE
    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    new_session.close.assert_not_awaited()
    runtime.send_status.assert_not_awaited()


async def test_current_detector_feed_exception_fails_closed_once() -> None:
    runtime = _Runtime()
    runtime._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    _install_ready_lifecycle(runtime, "qwen")
    runtime._asr_detector.feed = AsyncMock(
        side_effect=RuntimeError("current detector failed")
    )
    ingress = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = ingress

    result = await runtime._asr_runtime.submit(
        ProcessedVoiceFrame(b"\x01\x00" * 160, 16_000, 0.8, True),
        ingress_token=ingress,
    )
    await asyncio.sleep(0)

    assert result.status is AsrSubmitStatus.UNAVAILABLE
    codes = [
        json.loads(call.args[0])["code"] for call in runtime.send_status.await_args_list
    ]
    assert codes.count("ASR_INDEPENDENT_STREAM_FAILED") == 1
    assert runtime._asr_session is None
    assert runtime._asr_lifecycle is None
    assert runtime._asr_detector is None


async def test_stale_connect_failure_cannot_fail_new_generation() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_session = None
    started = asyncio.Event()
    release = asyncio.Event()
    candidate = SimpleNamespace(close=AsyncMock())

    async def connect() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("old candidate failed")

    candidate.connect = AsyncMock(side_effect=connect)
    runtime._asr_session_factory = MagicMock(return_value=candidate)
    runtime._asr_transport_selection = _selection("qwen")
    old_restart = asyncio.create_task(runtime._restart_transport())
    runtime._asr_transport_task = old_restart
    await asyncio.wait_for(started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    keep_transport = asyncio.Event()
    new_transport = asyncio.create_task(keep_transport.wait())
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    runtime._asr_transport_task = new_transport
    release.set()
    await asyncio.wait_for(old_restart, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    assert runtime._asr_transport_task is new_transport
    candidate.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    runtime.send_status.assert_not_awaited()
    keep_transport.set()
    await new_transport


async def test_close_unwind_cannot_clear_new_generation_owned_fields() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_detector = runtime._asr_detector
    assert old_detector is not None
    close_started = asyncio.Event()
    release_close = asyncio.Event()

    async def close_detector() -> None:
        close_started.set()
        await release_close.wait()

    old_detector.close = AsyncMock(side_effect=close_detector)
    runtime._asr_session_factory = object()
    runtime._asr_transport_selection = object()
    old_transport = asyncio.create_task(asyncio.Event().wait())
    runtime._asr_transport_task = old_transport
    closing = asyncio.create_task(runtime._asr_runtime._close_independent_asr())
    await asyncio.wait_for(close_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    keep_transport = asyncio.Event()
    new_transport = asyncio.create_task(keep_transport.wait())
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    runtime._asr_transport_task = new_transport
    new_token = runtime._capture_ingress_token()
    runtime._asr_runtime._asr_current_ingress_token = new_token
    new_transcript_dispatcher = runtime._asr_transcript_dispatcher
    new_detector_dispatcher = runtime._asr_detector_dispatcher
    new_audio_dispatcher = runtime._asr_audio_dispatcher
    release_close.set()
    await asyncio.wait_for(closing, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_current_ingress_token == new_token
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    assert runtime._asr_transport_task is new_transport
    assert runtime._asr_transcript_dispatcher is new_transcript_dispatcher
    assert runtime._asr_detector_dispatcher is new_detector_dispatcher
    assert runtime._asr_audio_dispatcher is new_audio_dispatcher
    old_detector.close.assert_awaited_once_with()
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    keep_transport.set()
    await new_transport


async def test_same_epoch_reconnect_survives_old_abort_release() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    assert lifecycle is not None
    assert old_detector is not None
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class _BlockingLease:
        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    runtime._asr_smart_turn_lease = _BlockingLease()
    aborting = asyncio.create_task(runtime._asr_runtime.abort("test_abort"))
    await asyncio.wait_for(release_started.wait(), 1)

    new_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    new_detector = _ReadyDetector()
    new_lease = _TestSmartTurnLease(object())
    runtime._asr_session = new_session
    runtime._asr_detector = new_detector
    runtime._asr_smart_turn_lease = new_lease
    lifecycle.invalidate_transport()
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()
    release_old_lease.set()
    await asyncio.wait_for(aborting, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_smart_turn_lease is new_lease
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    new_detector.reset.assert_not_awaited()
    assert new_lease.released is False


async def test_old_pipeline_failure_does_not_report_replacement_provider() -> None:
    runtime = _Runtime()
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.is_flushing_hot_swap_cache = False
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "provider-a"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def block_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=block_abort)
    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.process = AsyncMock(side_effect=RuntimeError("soxr failed"))
    processing = asyncio.create_task(
        runtime._process_microphone_stream_data(
            {
                "input_type": "audio",
                "sample_rate_hz": 48_000,
                "data": [1] * 480,
            },
            ingress_token=runtime._capture_ingress_token(),
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    runtime._voice_input_audio_pipeline = SimpleNamespace(
        process=AsyncMock(),
        close=AsyncMock(),
    )
    runtime._voice_input_pipeline_failed = False
    runtime._independent_asr_provider = "provider-b"
    runtime._set_microphone_route("independent")
    release_abort.set()
    await asyncio.wait_for(processing, 1)

    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "provider-b"
    assert runtime._voice_input_pipeline_failed is False
    runtime.send_status.assert_not_awaited()
    runtime._asr_runtime.abort.assert_awaited_once_with("audio_preprocessing_failed")


async def test_session_activation_resolves_asr_before_frontend_ack() -> None:
    order: list[str] = []
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.input_cache_lock = asyncio.Lock()
    manager.is_active = False
    manager._session_turn_count = 0
    manager.session_start_failure_count = 1
    manager.session_start_last_failure_time = 1.0
    manager._memory_error_retry_after = 1.0
    manager._session_start_circuit_open = True
    manager.pending_agent_callbacks = []
    manager._activity_tracker = type(
        "Tracker", (), {"on_voice_mode": lambda self, value: None}
    )()
    manager.is_goodbye_silent = lambda: False
    manager._drain_pending_context_appends_before_ready = AsyncMock()
    manager._flush_pending_input_data = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager._start_independent_asr_if_enabled = AsyncMock(
        side_effect=lambda _mode, **_kwargs: order.append("asr")
    )
    manager.send_session_started = AsyncMock(
        side_effect=lambda _mode: order.append("started")
    )

    stop = asyncio.Event()

    class _Session:
        async def handle_messages(self) -> None:
            await stop.wait()

    manager.session = _Session()

    await LLMSessionManager._start_session_activate(
        manager,
        "audio",
        0,
        time.time(),
    )

    assert order == ["asr", "started"]
    stop.set()
    await manager.message_handler_task


async def test_disabled_or_text_session_never_creates_provider(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    factory = MagicMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("text")

    factory.assert_not_called()
    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")


async def test_free_core_reports_unavailable_and_blocks_omni(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert "ASR_INDEPENDENT_UNAVAILABLE" in runtime.send_status.await_args.args[0]


async def test_provider_error_without_audio_closes_and_blocks_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_error(epoch, "glm")
    await asyncio.sleep(0)

    assert runtime._asr_session_epoch == epoch + 1
    assert runtime._asr_route_mode == "blocked"
    asr.close.assert_awaited_once_with()


async def test_blocked_route_consumes_audio_without_an_asr_or_omni_send() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "blocked"

    assert (
        await runtime._route_microphone_audio(
            b"\x00\x00",
            sample_rate_hz=16_000,
        )
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_independent_route_without_ready_session_blocks_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": False})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_settings_read_failure_blocks_omni(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=RuntimeError("settings unavailable")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "blocked"
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )


async def test_injection_failure_is_reported_once_without_provider_body() -> None:
    runtime = _Runtime()
    runtime.session.create_response.side_effect = RuntimeError("sensitive response")
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "hello",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    status_payloads = [call.args[0] for call in runtime.send_status.await_args_list]
    assert any("ASR_INDEPENDENT_INJECTION_FAILED" in item for item in status_payloads)
    assert "sensitive response" not in str(status_payloads)
    runtime.session.create_response.assert_awaited_once_with("hello")


async def test_session_swap_during_transcript_drops_old_final_injection() -> None:
    runtime = _Runtime()
    old_session = runtime.session
    new_session = type("Omni", (), {"create_response": AsyncMock()})()

    async def swap_session(*_args, **_kwargs) -> bool:
        runtime.session = new_session
        return True

    runtime.handle_input_transcript.side_effect = swap_session
    await _start_and_seal_turn(runtime, "glm")

    await runtime._handle_independent_asr_final(
        "belongs to old role",
        runtime._asr_session_epoch,
        "glm",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    # The registry already prepared this turn against old_session. A same-route
    # hot swap deliberately keeps the ingress token and utterance live, so the
    # final must finish on that pinned session instead of being dropped.
    old_session.create_response.assert_awaited_once_with("belongs to old role")
    new_session.create_response.assert_not_awaited()


async def test_game_takeover_during_transcript_drops_stale_core_final() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "glm")
    transcript_started = asyncio.Event()
    release_transcript = asyncio.Event()

    async def block_transcript(*_args, **_kwargs) -> bool:
        transcript_started.set()
        await release_transcript.wait()
        return True

    runtime.handle_input_transcript.side_effect = block_transcript
    runtime.session.submit_external_voice_turn = AsyncMock()
    runtime._asr_runtime.suspend = AsyncMock()
    turn_token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=turn_token,
        provider="glm",
        text="belongs to Core",
    )
    dispatch_task = asyncio.create_task(runtime._dispatch_core_asr_transcript(event))
    await asyncio.wait_for(transcript_started.wait(), 1)

    await runtime._suspend_independent_voice_input_for_game()
    release_transcript.set()
    await asyncio.wait_for(dispatch_task, 1)

    runtime.session.submit_external_voice_turn.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_status_delivery_failure_never_breaks_audio_runtime() -> None:
    runtime = _Runtime()
    runtime.send_status.side_effect = RuntimeError("socket closed")
    runtime._set_microphone_route("native")
    runtime.session.stream_audio = AsyncMock()
    identity = runtime._asr_runtime._capture_runtime_identity()

    await runtime._send_asr_status(
        "ASR_INDEPENDENT_READY",
        "glm",
        session_epoch=runtime._asr_session_epoch,
        expected_identity=identity,
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    runtime.send_status.assert_awaited_once()
    runtime.session.stream_audio.assert_awaited_once()
    assert runtime._voice_input_pipeline_failed is False


async def test_old_core_close_cannot_clear_new_pipeline_or_provider() -> None:
    runtime = _Runtime()
    runtime_close_entered = asyncio.Event()
    release_runtime_close = asyncio.Event()

    async def block_runtime_close() -> None:
        runtime_close_entered.set()
        await release_runtime_close.wait()

    runtime._asr_runtime.close = AsyncMock(side_effect=block_runtime_close)
    old_pipeline = runtime._voice_input_audio_pipeline
    old_pipeline.close = AsyncMock()
    runtime._independent_asr_provider = "old-provider"
    runtime._independent_asr_route_key = "old-core"
    runtime._set_microphone_route("independent")

    closing = asyncio.create_task(
        runtime._close_independent_asr(next_route_mode="blocked")
    )
    await asyncio.wait_for(runtime_close_entered.wait(), 1)
    new_pipeline = runtime._voice_input_audio_pipeline
    runtime._begin_asr_route_operation()
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    release_runtime_close.set()
    await asyncio.wait_for(closing, 1)

    old_pipeline.close.assert_awaited_once_with()
    assert runtime._voice_input_audio_pipeline is new_pipeline
    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"


async def test_stale_runtime_ready_result_cannot_replace_new_route(
    monkeypatch,
) -> None:
    start_entered = asyncio.Event()
    release_start = asyncio.Event()

    class BlockingBridge:
        def __init__(self) -> None:
            self.session_epoch = 0
            self.audio_generation = 0

        def capture_ingress_token(
            self,
            *,
            connection_id,
            lease_generation,
            route_generation,
        ):
            from main_logic.voice_turn.contracts import VoiceIngressToken

            return VoiceIngressToken(
                self.session_epoch,
                connection_id,
                lease_generation,
                route_generation,
                self.audio_generation,
            )

        async def close(self) -> None:
            self.session_epoch += 1
            self.audio_generation += 1

        async def start(self, **_kwargs) -> AsrStartResult:
            source_epoch = self.session_epoch
            start_entered.set()
            await release_start.wait()
            return AsrStartResult(
                AsrStartStatus.READY,
                provider="old-provider",
                session_epoch=source_epoch,
            )

    runtime = _Runtime()
    bridge = BlockingBridge()
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "old-core"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(start_entered.wait(), 1)
    runtime._begin_asr_route_operation()
    bridge.session_epoch += 1
    runtime.core_api_type = "new-core"
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    release_start.set()
    await asyncio.wait_for(starting, 1)

    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"


async def test_old_native_send_failure_cannot_close_new_session() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    send_entered = asyncio.Event()
    release_send = asyncio.Event()

    async def fail_old_send(_pcm16) -> None:
        send_entered.set()
        await release_send.wait()
        raise RuntimeError("connection closed")

    old_session = type("OldOmni", (), {})()
    old_session.stream_audio = AsyncMock(side_effect=fail_old_send)
    runtime.session = old_session
    old_token = runtime._capture_native_ingress_token()
    old_send = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00",
            sample_rate_hz=16_000,
            ingress_token=old_token,
        )
    )
    await asyncio.wait_for(send_entered.wait(), 1)

    new_session = type("NewOmni", (), {})()
    new_session.stream_audio = AsyncMock()
    runtime.session = new_session
    release_send.set()
    await asyncio.wait_for(old_send, 1)

    assert runtime.session_closed_by_server is False
    assert runtime._omni_mic_audio_bytes == 0
    await runtime._route_microphone_audio(
        b"\x02\x00",
        sample_rate_hz=16_000,
        ingress_token=runtime._capture_native_ingress_token(),
    )
    new_session.stream_audio.assert_awaited_once_with(b"\x02\x00")
    assert runtime._omni_mic_audio_bytes == 2


async def test_current_native_send_failure_still_closes_route_once() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.session.stream_audio = AsyncMock(
        side_effect=RuntimeError("connection closed")
    )

    await runtime._route_microphone_audio(
        b"\x01\x00",
        sample_rate_hz=16_000,
        ingress_token=runtime._capture_native_ingress_token(),
    )

    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00")
    assert runtime.session_closed_by_server is True
    assert runtime._omni_mic_audio_bytes == 0


async def test_partial_preview_requires_current_core_lease() -> None:
    runtime = _Runtime()
    websocket = type("WebSocket", (), {})()
    websocket.send_json = AsyncMock()
    runtime.websocket = websocket
    runtime._set_microphone_route("independent")
    epoch = runtime._asr_session_epoch
    token = VoiceTurnToken(
        ingress=runtime._capture_ingress_token(),
        turn_id=1,
    )
    stale_token = VoiceTurnToken(
        ingress=replace(token.ingress, session_epoch=epoch + 1),
        turn_id=token.turn_id,
    )

    runtime._voice_lease_owner = "game"
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="game")
    )
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = True
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="muted")
    )
    runtime._voice_lease_hard_muted = False
    runtime._voice_lease_focus_suppressed = True
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="focused")
    )
    runtime._voice_lease_focus_suppressed = False
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=stale_token, text="stale")
    )
    await runtime._send_core_asr_preview(
        VoicePartialEvent(turn_token=token, text="current")
    )

    websocket.send_json.assert_awaited_once_with(
        {
            "type": "user_transcript_preview",
            "text": "current",
            "turn_id": f"asr-preview-{epoch}",
        }
    )


async def test_old_notifications_cannot_override_new_generation() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    first_send_entered = asyncio.Event()
    release_first_send = asyncio.Event()
    payloads = []

    async def ordered_send_status(payload: str) -> None:
        payloads.append(json.loads(payload))
        if len(payloads) == 1:
            first_send_entered.set()
            await release_first_send.wait()

    runtime.send_status = AsyncMock(side_effect=ordered_send_status)
    old_epoch = runtime._asr_session_epoch
    old_event = AsrLifecycleNotification(
        state="local_listen",
        provider="old-provider",
        session_epoch=old_epoch,
    )
    old_delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(old_event))
    await asyncio.wait_for(first_send_entered.wait(), 1)

    runtime._asr_session_epoch += 1
    new_epoch = runtime._asr_session_epoch
    new_event = AsrLifecycleNotification(
        state="blocked",
        provider="new-provider",
        session_epoch=new_epoch,
    )
    new_delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(new_event))
    release_first_send.set()
    await asyncio.wait_for(
        asyncio.gather(old_delivery, new_delivery),
        1,
    )
    await runtime._send_core_asr_lifecycle(old_event)
    await runtime._send_core_asr_status(
        AsrStatusEvent(
            code="ASR_OLD_READY",
            provider="old-provider",
            session_epoch=old_epoch,
        )
    )
    await runtime._send_core_asr_status(
        AsrStatusEvent(
            code="ASR_NEW_READY",
            provider="new-provider",
            session_epoch=new_epoch,
        )
    )

    assert [
        payload["details"]["state"]
        for payload in payloads
        if payload["code"] == "ASR_LIFECYCLE_STATE"
    ] == ["local_listen", "blocked"]
    assert payloads[-1] == {
        "code": "ASR_NEW_READY",
        "details": {
            "provider": "new-provider",
            "session_epoch": new_epoch,
        },
    }
    assert payloads[1]["details"]["session_epoch"] == new_epoch


async def test_failure_event_only_blocks_current_generation() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="old-provider",
            session_epoch=current_epoch - 1,
        )
    )
    assert runtime._asr_route_mode == "independent"

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=current_epoch,
        )
    )
    assert runtime._asr_route_mode == "blocked"


async def test_runtime_failure_from_a_live_route_still_revokes_the_lease() -> None:
    # Codex P2. The chokepoint refactor passed the PRE-transition identity tuple
    # as still_current, but that tuple carries _asr_route_mode (and
    # _microphone_route_generation, inside the ingress token) while the handler
    # sets the route to "blocked" two lines earlier. The predicate was therefore
    # false on ENTRY -- against the handler's own step -- so
    # _fail_closed_voice_route returned before revoking, leaving the recording
    # socket holding a live hardware microphone on a dead route. Only reachable
    # from a LIVE route, which is exactly the real runtime-failure case; an
    # already-blocked route happened to compare equal and masked it.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    runtime._voice_input_websocket = object()

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_lease_connection_id == ""


async def test_runtime_failure_still_fences_a_competing_newer_operation() -> None:
    # Re-basing the identity must not weaken the fence it exists for: a NEWER
    # route operation landing during this handler's own transition still has to
    # stop the revoke, because _revoke_voice_input_connection calls
    # _invalidate_asr_start() and would cancel that newer start.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    original_set_route = runtime._set_microphone_route

    def _set_route_then_supersede(mode: str) -> None:
        original_set_route(mode)
        runtime._begin_asr_route_operation()

    runtime._set_microphone_route = _set_route_then_supersede

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._voice_lease_connection_id == "socket-a"


async def test_runtime_failure_leaves_the_game_lease_alone() -> None:
    # The galgame route holds the mic through its built-in consumer route and tears
    # down via GAME_ROUTE_ENDED; re-basing the identity must not start
    # collaterally revoking it.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    runtime._voice_lease_owner = "game"

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._voice_lease_connection_id == "socket-a"


@pytest.mark.parametrize(
    "transition",
    [
        "hard_mute",
        "focus_suppress",
        "game_takeover",
        "lease_sync",
        "connection_replacement",
    ],
)
async def test_core_start_is_invalidated_by_mic_lease_transition(
    monkeypatch,
    transition: str,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    release_connect = asyncio.Event()

    class Candidate:
        def __init__(self) -> None:
            self.connect_started = asyncio.Event()
            self.is_ready = True
            self.close = AsyncMock()

        async def connect(self) -> None:
            self.connect_started.set()
            await release_connect.wait()

    candidate = Candidate()
    selection = SimpleNamespace(
        provider_key="qwen",
        endpointing_mode="provider",
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=candidate),
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 0

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(candidate.connect_started.wait(), 1)
    if transition == "connection_replacement":
        runtime._begin_voice_input_connection("replacement")
    elif transition == "lease_sync":
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
    else:
        await runtime._handle_voice_input_control(transition, 1)
    release_connect.set()
    await asyncio.wait_for(starting, 1)

    assert runtime._asr_route_mode == "blocked"
    assert runtime._independent_asr_provider is None
    assert "ASR_INDEPENDENT_READY" not in str(runtime.send_status.await_args_list)
    candidate.close.assert_awaited_once_with()


async def test_settings_result_is_stale_after_connection_replacement(
    monkeypatch,
) -> None:
    settings_started = asyncio.Event()
    release_settings = asyncio.Event()

    async def load_settings(**_kwargs):
        settings_started.set()
        await release_settings.wait()
        return {"independentAsrEnabled": True}

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 0
    runtime._asr_runtime.start = AsyncMock(
        return_value=AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        load_settings,
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(settings_started.wait(), 1)
    runtime._begin_voice_input_connection("replacement")
    release_settings.set()
    await asyncio.wait_for(starting, 1)

    runtime._asr_runtime.start.assert_not_awaited()
    assert runtime._asr_route_mode == "blocked"
    assert runtime._independent_asr_provider is None


@pytest.mark.parametrize("enabled", [False, True])
async def test_cold_start_with_unclaimed_lease_still_routes(
    monkeypatch, enabled: bool
) -> None:
    """The bundled frontend flips the lease owner to "core" only after
    session_started, so route setup must not require owner=="core": gating
    the start on lease state would leave every cold start blocked."""

    runtime = _Runtime()
    runtime._voice_lease_owner = "none"
    runtime._voice_lease_synchronized = True
    runtime.core_api_type = "qwen"

    async def ready_start(**_kwargs) -> AsrStartResult:
        return AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )

    runtime._asr_runtime.start = AsyncMock(side_effect=ready_start)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": enabled}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    if enabled:
        assert runtime._asr_route_mode == "independent"
        assert runtime._independent_asr_provider == "qwen"
    else:
        assert runtime._asr_route_mode == "native"


async def test_stale_start_abort_does_not_clobber_newer_start_placeholder(
    monkeypatch,
) -> None:
    """A stale start parked in its abort must not clear the blocked
    placeholder a newer start installed meanwhile: clearing it would make
    the newer start's fence fail before it even reaches the native
    fallback, leaving the route blocked with no failure status."""

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime._voice_lease_connection_id = "conn-A"
    runtime._voice_lease_generation = 0

    a_start_parked = asyncio.Event()
    release_a_start = asyncio.Event()
    a_abort_parked = asyncio.Event()
    release_a_abort = asyncio.Event()
    b_settings_parked = asyncio.Event()
    release_b_settings = asyncio.Event()
    start_calls: list[dict] = []

    async def fake_runtime_start(**kwargs):
        start_calls.append(kwargs)
        if len(start_calls) == 1:
            a_start_parked.set()
            await release_a_start.wait()
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code="ASR_START_STALE",
                session_epoch=runtime._asr_session_epoch,
            )
        return AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )

    async def fake_abort(reason):
        a_abort_parked.set()
        await release_a_abort.wait()

    runtime._asr_runtime.start = fake_runtime_start
    runtime._asr_runtime.abort = fake_abort
    runtime._asr_runtime.close = AsyncMock()

    settings_calls = 0

    async def load_settings(**_kwargs):
        nonlocal settings_calls
        settings_calls += 1
        if settings_calls == 2:
            b_settings_parked.set()
            await release_b_settings.wait()
        return {"independentAsrEnabled": True}

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        load_settings,
    )

    task_a = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(a_start_parked.wait(), 1)
    runtime._begin_voice_input_connection("conn-B")
    release_a_start.set()
    await asyncio.wait_for(a_abort_parked.wait(), 1)

    task_b = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(b_settings_parked.wait(), 1)
    assert runtime._independent_asr_route_key == "qwen"

    release_a_abort.set()
    await asyncio.wait_for(task_a, 1)
    assert runtime._independent_asr_route_key == "qwen"

    release_b_settings.set()
    await asyncio.wait_for(task_b, 1)

    assert len(start_calls) == 2
    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "qwen"


async def test_core_start_survives_benign_lease_transition(monkeypatch) -> None:
    """Owner flip / mute toggle / lease bump during the settings await are
    PCM-gating changes, not route operations; they must not abort the start
    (there is no retry or failure status on that path)."""

    settings_started = asyncio.Event()
    release_settings = asyncio.Event()

    async def load_settings(**_kwargs):
        settings_started.set()
        await release_settings.wait()
        return {"independentAsrEnabled": True}

    runtime = _Runtime()
    runtime._voice_lease_owner = "none"
    runtime._voice_lease_synchronized = True
    runtime.core_api_type = "qwen"

    async def ready_start(**_kwargs) -> AsrStartResult:
        return AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )

    runtime._asr_runtime.start = AsyncMock(side_effect=ready_start)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        load_settings,
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(settings_started.wait(), 1)
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = True
    runtime._voice_lease_generation += 1
    release_settings.set()
    await asyncio.wait_for(starting, 1)

    runtime._asr_runtime.start.assert_awaited_once()
    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "qwen"


@pytest.mark.parametrize(
    "newer_transition",
    [
        "game_takeover",
        "hard_mute",
        "focus_suppress",
        "lease_generation",
        "connection_replacement",
    ],
)
async def test_game_release_resume_only_survives_pcm_gating_transitions(
    newer_transition: str,
) -> None:
    """Ownership loss during the release abort must skip resume; PCM-gating
    transitions (mute/focus/lease bump) must not, because resume has no other
    call site and skipping it leaves the runtime SUSPENDED for the session."""
    runtime = _Runtime()
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 1
    runtime._voice_lease_owner = "game"
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def abort(reason: str) -> None:
        if reason == "game_release":
            abort_started.set()
            await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=abort)
    runtime._asr_runtime.resume = AsyncMock()
    runtime._asr_runtime.suspend = AsyncMock()
    releasing = asyncio.create_task(
        runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
            reason="game_release",
            force_abort=True,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    if newer_transition == "connection_replacement":
        runtime._begin_voice_input_connection("replacement")
    elif newer_transition == "lease_generation":
        runtime._voice_lease_generation += 1
    elif newer_transition == "game_takeover":
        await runtime._apply_voice_lease_state(
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
            reason="game_takeover",
            force_abort=True,
        )
    elif newer_transition == "hard_mute":
        await runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
            reason="hard_mute",
            force_abort=True,
        )
    else:
        await runtime._apply_voice_lease_state(
            owner="core",
            hard_muted=False,
            focus_suppressed=True,
            reason="focus_suppress",
            force_abort=True,
        )
    release_abort.set()
    await asyncio.wait_for(releasing, 1)

    if newer_transition in {"game_takeover", "connection_replacement"}:
        runtime._asr_runtime.resume.assert_not_awaited()
    else:
        runtime._asr_runtime.resume.assert_awaited_once_with("game_release")


async def test_current_game_release_still_aborts_and_resumes_once() -> None:
    runtime = _Runtime()
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 1
    runtime._voice_lease_owner = "game"
    runtime._asr_runtime.abort = AsyncMock()
    runtime._asr_runtime.resume = AsyncMock()

    await runtime._apply_voice_lease_state(
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
        reason="game_release",
        force_abort=True,
    )

    runtime._asr_runtime.abort.assert_awaited_once_with("game_release")
    runtime._asr_runtime.resume.assert_awaited_once_with("game_release")


@pytest.mark.parametrize("notification", ["status", "lifecycle", "failure"])
async def test_notification_waiting_on_lock_drops_same_epoch_stale_identity(
    notification: str,
) -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch
    await runtime._asr_notification_lock.acquire()
    if notification == "status":
        event = AsrStatusEvent(
            code="ASR_OLD_READY",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._send_core_asr_status(event))
    elif notification == "lifecycle":
        event = AsrLifecycleNotification(
            state="local_listen",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(event))
    else:
        event = AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._handle_core_asr_failure(event))
    await asyncio.sleep(0)

    runtime._asr_audio_generation += 1
    runtime._asr_notification_lock.release()
    await asyncio.wait_for(delivery, 1)

    runtime.send_status.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"


async def test_failure_cancellation_can_publish_without_notification_deadlock() -> (
    None
):
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch

    async def cancellation_wait_idle() -> None:
        assert runtime._asr_notification_lock.locked() is False
        await runtime._send_core_asr_status(
            AsrStatusEvent(
                code="ASR_CANCEL_CLEANUP",
                provider="plugin-consumer",
                session_epoch=current_epoch,
            )
        )

    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=cancellation_wait_idle
    )

    await asyncio.wait_for(
        runtime._handle_core_asr_failure(
            AsrFailureEvent(
                code="ASR_INDEPENDENT_FAILED",
                provider="current-provider",
                session_epoch=current_epoch,
            )
        ),
        1,
    )

    assert "ASR_CANCEL_CLEANUP" in str(runtime.send_status.await_args_list)


def _lease_resync_statuses(runtime: _Runtime) -> list[dict]:
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    return [
        status
        for status in statuses
        if status["code"] == "VOICE_INPUT_LEASE_RESYNC_REQUIRED"
    ]


def _mic_frame() -> dict:
    return {"input_type": "audio", "sample_rate_hz": 16_000, "data": [1] * 160}


async def test_unsynchronized_pcm_signals_lease_resync_once_per_state() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True

    for _ in range(3):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    resync = _lease_resync_statuses(runtime)
    assert len(resync) == 1
    assert resync[0]["details"]["reason"] == "lease_unsynchronized"
    assert runtime._audio_stream_queue.empty()
    assert runtime._audio_stream_worker_task is None

    assert runtime._begin_voice_input_connection("pet-window") is True
    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert len(_lease_resync_statuses(runtime)) == 2
    assert runtime._audio_stream_queue.empty()


async def test_synchronized_none_owner_pcm_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    resync = _lease_resync_statuses(runtime)
    assert len(resync) == 1
    assert resync[0]["details"]["reason"] == "owner_none"
    assert runtime._audio_stream_queue.empty()


async def test_hard_muted_pcm_never_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert _lease_resync_statuses(runtime) == []
    assert runtime._audio_stream_queue.empty()


async def test_game_owner_pcm_never_signals_lease_resync() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )

    for _ in range(2):
        await runtime._enqueue_audio_stream_data(_mic_frame())

    assert _lease_resync_statuses(runtime) == []
    assert runtime._audio_stream_queue.empty()


async def test_noise_reduction_disabled_reaches_pipeline_audio_processor(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    created: list[dict] = []

    class _RecordingProcessor:
        def __init__(self, **kwargs) -> None:
            created.append(kwargs)
            self.speech_probability = 0.0
            self.rnnoise_available = False

        def process_chunk(self, _audio_bytes: bytes) -> bytes:
            return b""

        def close(self) -> None:
            return None

    monkeypatch.setattr(audio_input_module, "AudioProcessor", _RecordingProcessor)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": False,
                "noiseReductionEnabled": False,
            }
        ),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._voice_input_noise_reduction_enabled is False
    assert runtime._voice_input_audio_pipeline.nr_enabled is False
    await runtime._voice_input_audio_pipeline.process(
        b"\x01\x00" * 480,
        sample_rate_hz=48_000,
    )
    assert created[-1]["noise_reduce_enabled"] is False

    started_pipeline = runtime._voice_input_audio_pipeline
    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._voice_input_audio_pipeline is not started_pipeline
    assert runtime._voice_input_audio_pipeline.nr_enabled is False
    await runtime._voice_input_audio_pipeline.process(
        b"\x01\x00" * 480,
        sample_rate_hz=48_000,
    )
    assert len(created) == 2
    assert created[-1]["noise_reduce_enabled"] is False


async def test_settings_read_failure_keeps_noise_reduction_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=RuntimeError("settings unavailable")),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._voice_input_noise_reduction_enabled is True
    assert runtime._voice_input_audio_pipeline.nr_enabled is True


async def test_idle_backpressure_trailing_activity_is_dropped_cleanly(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    token = runtime._capture_ingress_token()
    component._asr_current_ingress_token = token
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)

    await component._handle_audio_ingress_backpressure(token)

    # The idle branch bumps the audio generation but keeps the session
    # adopted so genuinely new speech keeps working.
    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_session is sessions[0]
    assert not component._ingress_token_matches(token)

    # Trailing session-side speech events with the stale generation must be
    # dropped cleanly: without the identity gate the first event corrupts the
    # lifecycle toward ACTIVE and the second raises an uncaught
    # ASR_INGRESS_TOKEN_REQUIRED into the provider adapter.
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert component._asr_turn_prepared is False
    runtime.session.handle_interruption.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    assert all(
        "ASR_INDEPENDENT_FAILED" not in call.args[0]
        for call in runtime.send_status.await_args_list
    )


async def test_idle_backpressure_new_speech_still_wakes_adopted_session(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    stale_token = runtime._capture_ingress_token()
    component._asr_current_ingress_token = stale_token
    on_activity = callbacks[0]["on_speech_activity"]

    await component._handle_audio_ingress_backpressure(stale_token)

    # New speech re-arms the current ingress token through submit() before
    # the provider observes it; the adopted session must then wake normally.
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    assert component._asr_turn_prepared is True
    runtime.handle_new_message.assert_awaited_once()


async def test_accepted_final_dropped_by_generation_bump_abandons_turn(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    lifecycle = component._asr_lifecycle
    assert lifecycle is not None
    runtime.session.abandon_external_voice_turn = MagicMock()
    component._asr_current_ingress_token = runtime._capture_ingress_token()
    epoch = component._asr_session_epoch
    on_activity = callbacks[0]["on_speech_activity"]
    on_final = callbacks[0]["on_input_transcript"]

    await on_activity(SpeechActivityEvent.SPEECH_STARTED)
    assert lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sealed_turn_id = lifecycle.snapshot.turn_id
    await component._handle_independent_asr_endpoint(epoch)
    assert lifecycle.snapshot.state is VoiceLifecycleState.DRAINING

    await on_final("hello world")

    # The final was accepted, but the generation moves on before the serial
    # transcript dispatcher delivers the queued envelope.
    component._asr_audio_generation += 1
    await component.wait_transcript_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-{sealed_turn_id}"
    )


async def test_accepted_final_identity_loss_before_dispatch_abandons_turn() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    component = runtime._asr_runtime
    epoch = component._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    sealed_turn_id = component._asr_lifecycle.snapshot.turn_id
    lease = component._asr_smart_turn_lease
    assert lease is not None

    async def bumping_release() -> None:
        component._asr_audio_generation += 1

    lease.release = bumping_release

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{epoch}-{sealed_turn_id}"
    )


async def test_failed_lease_release_does_not_skip_accepted_final_delivery() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    component = runtime._asr_runtime
    epoch = component._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")
    lease = component._asr_smart_turn_lease
    assert lease is not None

    async def raising_release() -> None:
        raise RuntimeError("release boom")

    lease.release = raising_release

    await runtime._handle_independent_asr_final("hello", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    assert component._asr_smart_turn_lease is None
    assert (
        component._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
    )
    runtime.handle_input_transcript.assert_awaited_once_with(
        "hello",
        is_voice_source=True,
        source="independent_asr",
        metadata={"provider": "qwen"},
    )
    assert component._asr_warm_expiry_task is not None
    component._asr_warm_expiry_task.cancel()


async def test_transport_restart_task_failure_is_logged(caplog) -> None:
    runtime = _Runtime()
    component = runtime._asr_runtime

    async def failing_restart() -> None:
        raise RuntimeError("restart boom")

    component._restart_transport = failing_restart
    with caplog.at_level(logging.ERROR, logger="main_logic.asr_client._infra"):
        component._ensure_transport_restart_task()
        task = component._asr_transport_task
        assert task is not None
        await asyncio.wait([task])
        await asyncio.sleep(0)

    assert "independent-asr-transport-restart" in caplog.text
    assert "restart boom" in caplog.text


async def test_start_resolves_selection_off_event_loop(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    selection = _selection("qwen", "provider")
    resolver_threads: list[threading.Thread] = []

    def resolver(core_type: str):
        assert core_type == "qwen"
        resolver_threads.append(threading.current_thread())
        return selection

    session = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, **_kwargs: session,
    )
    detector_factory = MagicMock(return_value=_ReadyDetector())
    monkeypatch.setattr(runtime_module, "DetectorRuntime", detector_factory)

    result = await runtime._asr_runtime.start(
        route_key="qwen",
        resource_optimization_enabled=False,
    )

    assert result.status is AsrStartStatus.READY
    assert len(resolver_threads) == 1
    assert resolver_threads[0] is not threading.main_thread()
    assert (
        detector_factory.call_args.kwargs["resource_optimization_enabled"] is False
    )


async def test_teardown_routines_share_one_turn_state_reset() -> None:
    import ast
    import inspect as inspect_module

    from main_logic.asr_client import runtime as runtime_module

    source = inspect_module.getsource(runtime_module.IndependentAsrRuntime)
    tree = ast.parse(source)
    class_node = tree.body[0]
    for method_name in (
        "_close_independent_asr",
        "_abort_transport",
        "_handle_independent_asr_error",
    ):
        method = next(
            node
            for node in class_node.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == method_name
        )
        calls = {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert "_reset_asr_turn_state" in calls, method_name


def test_hot_swap_replay_damage_accounts_for_rebound_frames() -> None:
    # Codex P2. Cached pre-swap frames carry a stale route generation, so replay
    # rebinds them onto the new session -- but only the local SEND token is
    # rebound; the frame objects appended to damaged_frames keep their original
    # token. The final `any(_ingress_token_matches(frame.token) ...)` check was
    # therefore false, _invalidate_interrupted_voice_turn was skipped, and a
    # prefix that had already reached the new provider stayed in place: later
    # speech got concatenated across the missing tail instead of the damaged
    # turn being cleared.
    #
    # Structural, and deliberately so: driving _flush_hot_swap_audio_cache to a
    # mid-replay failure needs a cache, live session, route mode and token
    # generations. What this pins is that the rebind records current-route
    # damage and that the damage check consults it.
    import inspect

    from main_logic.core import asr_runtime as asr_runtime_module

    source = inspect.getsource(asr_runtime_module.AsrRuntimeMixin._flush_hot_swap_audio_cache)

    assert "rebound_to_current_route = False" in source, (
        "the flush must track whether any frame was rebound onto the live route"
    )
    assert "nonlocal rebound_to_current_route" in source, (
        "replay_frames must be able to record the rebind"
    )
    # Set at the rebind, consulted at the damage check, in that order.
    set_at = source.index("rebound_to_current_route = True")
    # Anchor on the damage condition itself: a bare-name match also hits the
    # `nonlocal` declaration, which sits BEFORE the rebind and inverted this
    # ordering assertion into a false failure.
    checked_at = source.index("if damaged_frames and (")
    assert source.index("token = rebound") < set_at, (
        "the flag belongs with the rebind it records"
    )
    assert set_at < checked_at
    assert "_invalidate_interrupted_voice_turn" in source[checked_at:], (
        "the damage check must still be what gates the invalidation"
    )

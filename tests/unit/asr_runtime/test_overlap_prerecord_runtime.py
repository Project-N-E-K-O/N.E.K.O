import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
import pytest
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult, DetectorRuntime
from main_logic.asr_client.lifecycle import AudioDisposition, VoiceLifecycleState, VoiceRouteMode
from main_logic.asr_client.lifecycle import VoiceInputLifecycleController
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent
from main_logic.voice_turn.contracts import EvaluationStatus, TurnDecision
from main_logic.asr_client.endpointing.coordinator import CoordinatorState
from main_logic.asr_client.endpointing.detector import CoreDetectorEventEnvelope

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


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
    runtime._asr_provider = "glm"
    runtime._asr_route_mode = "independent"
    lifecycle = VoiceInputLifecycleController(
        provider_policy=resolve_provider_policy("glm", "manual"),
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
        provider_policy=resolve_provider_policy("glm", "manual"),
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


async def test_provider_final_preserves_unconfirmed_successor_pcm_as_pre_roll() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "openai")
    detector = runtime._asr_detector
    assert isinstance(detector, _ReadyDetector)
    detector.complete_provider_candidate.return_value = True
    epoch = runtime._asr_session_epoch

    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    await runtime._handle_independent_asr_endpoint(epoch)
    successor_pcm = b"\x02\x00" * 160
    assert runtime._asr_lifecycle.accept_audio(
        successor_pcm,
        sample_rate_hz=16_000,
    ).disposition is AudioDisposition.BUFFER

    await runtime._handle_independent_asr_final("first", epoch, "openai")
    await runtime._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED,
        epoch,
    )
    decision = runtime._asr_lifecycle.accept_audio(
        b"\x03\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
    assert decision.pre_roll.startswith(successor_pcm)
    detector.complete_provider_candidate.assert_awaited_once()


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


@pytest.mark.unit
async def test_a_stale_onset_does_not_evict_the_prerecord_buffer() -> None:
    """Trimming is only safe against an onset this turn actually owns.

    A leftover value from an older turn would otherwise look like "speech began
    long ago" and evict every frame captured since -- the opposite of what the
    trim exists for. The buffer uses the same trust window as the record, so
    the two agree on where the turn began.
    """
    runtime = _Runtime()
    runtime._asr_route_mode = "independent"
    now = time.monotonic()

    # 不可信的 onset。取**未来**时刻这一侧：那是真正危险的方向 —— 拿它去裁，
    # 每一帧都「早于开口」，整个缓冲会被清空。（过去那一侧的残值只会裁掉比它
    # 更早的帧，多数情况下是空操作，判别不出这条守卫。）
    runtime._asr_runtime._asr_turn_onset_at = now + 30.0

    for i in range(3):
        assert runtime._stage_independent_visual_frame(
            f"frame-{i}",
            source="screen",
            request_id=f"screen-{i}",
            captured_at=now - 1.0 + i * 0.2,
        )

    # onset 不可信 → 不裁，三张都留着（若采信，三张会被全部清掉）。
    assert [f.image_b64 for f in runtime._prerecord_visual_frames] == [
        "frame-0",
        "frame-1",
        "frame-2",
    ]

import hashlib
import pytest
from main_logic.asr_client.runtime import AsrRuntimeCallbacks, AsrStartStatus, IndependentAsrRuntime
from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from main_logic.voice_turn.contracts import AsrFailureEvent, AsrLifecycleNotification, AsrStatusEvent, AsrSubmitStatus, SpeechActivityEvent, VoicePartialEvent, VoiceTranscriptEvent

from tests.support.asr_fakes import (
    _selection,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_speaker_shadow_abba_cannot_change_provider_authority(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    quiet = b"\x00\x00" * 160
    started = b"\x01\x00" * 160
    continued = b"\x02\x00" * 160
    paused = b"\x03\x00" * 160
    successor = b"\x04\x00" * 160
    candidate_frames = (quiet, started, continued, paused)
    candidate_probabilities = (0.0, 0.9, 0.1, 0.1)
    candidate_rnnoise_available = (False, True, True, True)
    candidate_processed_frames = tuple(
        ProcessedVoiceFrame(
            pcm16=pcm16,
            sample_rate_hz=16_000,
            speech_probability=probability,
            rnnoise_available=rnnoise_available,
        )
        for pcm16, probability, rnnoise_available in zip(
            candidate_frames,
            candidate_probabilities,
            candidate_rnnoise_available,
            strict=True,
        )
    )
    successor_processed_frame = ProcessedVoiceFrame(
        pcm16=successor,
        sample_rate_hz=16_000,
        speech_probability=0.9,
        rnnoise_available=True,
    )
    gate_events = (
        (),
        (SpeechActivityEvent.SPEECH_STARTED,),
        (),
        (SpeechActivityEvent.CANDIDATE_PAUSE,),
        (SpeechActivityEvent.SPEECH_STARTED,),
    )
    # Metrics derived from wall-clock, excluded from the snapshot comparison
    # because their value depends on how the runner happened to schedule us.
    #
    # ⚠️ Audio-duration metrics (local_audio_ms / cloud_audio_ms /
    # provider_wire_audio_ms / suppressed_silence_ms / shadow_suppressed_audio_ms)
    # are deliberately NOT here: they are computed from the frames fed in, so they
    # are deterministic and are part of what this test asserts.
    #
    # ⚠️ Any new wall-clock metric must be added here. Nothing on the dataclass
    # marks a field as wall-clock, so this list cannot be derived — which is how
    # asr_audio_command_queue_ms was missed when it landed: it measures
    # `time.monotonic() - queued_at` (asr_client/audio.py), reads 0 on an idle
    # machine, and came back as 16 (the Windows timer granularity) on a busy CI
    # runner, failing whole-snapshot equality on a single value.
    volatile_metric_names = frozenset(
        {
            "connect_latency_ms",
            "first_partial_latency_ms",
            "final_latency_ms",
            "smart_turn_load_ms",
            "smart_turn_inference_ms",
            "detector_submit_latency_ms",
            "detector_queue_audio_ms",
            "detector_queue_high_water_ms",
            "detector_oldest_frame_age_ms",
            "asr_audio_command_queue_ms",
        }
    )
    real_detector_runtime = DetectorRuntime
    selection = _selection("qwen", "provider")

    class _Vad:
        def __init__(self) -> None:
            self.load_count = 0
            self.close_count = 0

        def load(self) -> bool:
            self.load_count += 1
            return True

        def close(self) -> None:
            self.close_count += 1

    class _Gate:
        def __init__(self) -> None:
            self.frames: list[bytes] = []
            self._events = iter(gate_events)

        def feed(self, pcm16: bytes):
            self.frames.append(pcm16)
            return next(self._events)

        def reset(self) -> None:
            return None

    class _ProviderSession:
        def __init__(self, callbacks: dict[str, object]) -> None:
            self.callbacks = callbacks
            self.is_ready = True
            self.connect_count = 0
            self.close_count = 0
            self.signal_end_count = 0
            self.provider_wire_audio_ms = 0
            self.wire_pcm: list[bytes] = []

        async def connect(self) -> None:
            self.connect_count += 1

        async def close(self) -> None:
            self.close_count += 1

        async def stream_audio(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
        ) -> None:
            assert sample_rate_hz == 16_000
            self.wire_pcm.append(pcm16)
            self.provider_wire_audio_ms += len(pcm16) * 1_000 // (16_000 * 2)

        async def signal_user_activity_end(self) -> None:
            self.signal_end_count += 1

    class _Shadow:
        enabled = True

        def __init__(self, *, raises: bool) -> None:
            self.raises = raises
            self.submissions: list[tuple[bytes, int, object]] = []
            self.finishes: list[object] = []
            self.reset_count = 0
            self.close_count = 0

        def submit(
            self,
            pcm16: bytes,
            *,
            sample_rate_hz: int,
            candidate,
        ) -> bool:
            self.submissions.append((pcm16, sample_rate_hz, candidate))
            if self.raises and len(self.submissions) == 2:
                raise RuntimeError("shadow submit failure")
            return False

        def finish_candidate(self, candidate) -> bool:
            self.finishes.append(candidate)
            if self.raises:
                raise RuntimeError("shadow finish failure")
            return False

        async def reset(self) -> None:
            self.reset_count += 1

        async def close(self) -> None:
            self.close_count += 1

    def turn_identity(turn_token: VoiceTurnToken) -> tuple[object, ...]:
        ingress = turn_token.ingress
        return (
            turn_token.turn_id,
            ingress.session_epoch,
            ingress.connection_id,
            ingress.lease_generation,
            ingress.route_generation,
            ingress.audio_generation,
        )

    def transcript_identity(event: VoiceTranscriptEvent) -> tuple[object, ...]:
        return (event.text, event.provider, *turn_identity(event.turn_token))

    async def replay(
        shadow_mode: str | None,
    ) -> tuple[dict[str, object], _Shadow | None]:
        lifecycle_notifications: list[AsrLifecycleNotification] = []
        statuses: list[AsrStatusEvent] = []
        failures: list[AsrFailureEvent] = []
        prepared_turns: list[VoiceTurnToken] = []
        finals: list[VoiceTranscriptEvent] = []
        abandoned_turns: list[VoiceTurnToken] = []
        partials: list[VoicePartialEvent] = []
        gate = _Gate()
        vad = _Vad()
        provider_sessions: list[_ProviderSession] = []
        detector_shadows: list[object | None] = []
        factory_calls = 0
        shadow = (
            None
            if shadow_mode is None
            else _Shadow(raises=shadow_mode == "raises")
        )

        async def on_prepare_turn(turn_token: VoiceTurnToken) -> bool:
            prepared_turns.append(turn_token)
            return True

        async def on_partial(event: VoicePartialEvent) -> None:
            partials.append(event)

        async def on_final(event: VoiceTranscriptEvent) -> None:
            finals.append(event)

        async def on_turn_abandoned(turn_token: VoiceTurnToken) -> None:
            abandoned_turns.append(turn_token)

        async def on_failure(event: AsrFailureEvent) -> None:
            failures.append(event)

        async def on_status(event: AsrStatusEvent) -> None:
            statuses.append(event)

        async def on_lifecycle(event: AsrLifecycleNotification) -> None:
            lifecycle_notifications.append(event)

        runtime = IndependentAsrRuntime(
            AsrRuntimeCallbacks(
                display_name=lambda: "ABBA",
                on_prepare_turn=on_prepare_turn,
                on_partial=on_partial,
                on_final=on_final,
                on_turn_abandoned=on_turn_abandoned,
                on_failure=on_failure,
                on_status=on_status,
                on_lifecycle=on_lifecycle,
            )
        )

        def create_session(_core_type: str, **kwargs) -> _ProviderSession:
            assert kwargs["selection"] is selection
            session = _ProviderSession(kwargs)
            provider_sessions.append(session)
            return session

        def create_detector(**kwargs) -> DetectorRuntime:
            detector_shadows.append(kwargs.get("speaker_shadow"))
            return real_detector_runtime(vad=vad, gate=gate, **kwargs)

        def create_shadow():
            nonlocal factory_calls
            factory_calls += 1
            return shadow

        monkeypatch.setattr(
            runtime_module,
            "_resolve_asr_selection",
            lambda _core_type: selection,
        )
        monkeypatch.setattr(
            runtime_module,
            "_create_asr_session_from_selection",
            create_session,
        )
        monkeypatch.setattr(runtime_module, "DetectorRuntime", create_detector)

        start_result = await runtime.start(
            route_key="qwen",
            resource_optimization_enabled=True,
            speaker_shadow_factory=(create_shadow if shadow is not None else None),
        )
        assert start_result.status is AsrStartStatus.READY
        assert len(provider_sessions) == 1
        provider_session = provider_sessions[0]
        lifecycle = runtime._asr_lifecycle
        assert lifecycle is not None
        ingress_token = runtime.capture_ingress_token(
            connection_id="abba-connection",
            lease_generation=7,
            route_generation=11,
        )
        snapshots: list[tuple[object, ...]] = []

        def record_snapshot(step: str) -> None:
            snapshot = lifecycle.snapshot
            snapshots.append(
                (
                    step,
                    snapshot.state.value,
                    snapshot.route_mode.value,
                    snapshot.route_generation,
                    snapshot.transport_generation,
                    snapshot.turn_id,
                    tuple(
                        sorted(
                            (name, value)
                            for name, value in lifecycle.metrics.snapshot().items()
                            if name not in volatile_metric_names
                        )
                    ),
                )
            )

        record_snapshot("started")
        submit_statuses: list[AsrSubmitStatus] = []
        for index, processed_frame in enumerate(candidate_processed_frames):
            result = await runtime.submit(
                processed_frame,
                ingress_token=ingress_token,
            )
            await runtime._asr_audio_dispatcher.wait_idle()
            submit_statuses.append(result.status)
            record_snapshot(f"candidate-frame-{index}")

        endpoint_result = await provider_session.callbacks["on_turn_endpointed"]()
        record_snapshot("endpoint")
        provider_wire_before_successor = tuple(provider_session.wire_pcm)
        shadow_submissions_before_successor = (
            () if shadow is None else tuple(shadow.submissions)
        )

        successor_result = await runtime.submit(
            successor_processed_frame,
            ingress_token=ingress_token,
        )
        await runtime._asr_audio_dispatcher.wait_idle()
        submit_statuses.append(successor_result.status)
        record_snapshot("successor-buffered")
        provider_wire_before_final = tuple(provider_session.wire_pcm)
        shadow_submissions_before_final = (
            () if shadow is None else tuple(shadow.submissions)
        )

        final_result = await provider_session.callbacks["on_input_transcript"](
            "abba-final"
        )
        await runtime._asr_audio_dispatcher.wait_idle()
        await runtime.wait_transcript_idle()
        record_snapshot("final-successor-active")

        await runtime.close()
        record_snapshot("closed")
        trace = {
            "start_result": (
                start_result.status.value,
                start_result.provider,
                start_result.failure_code,
                start_result.session_epoch,
            ),
            "wire_pcm": tuple(provider_session.wire_pcm),
            "wire_sha256": tuple(
                hashlib.sha256(payload).hexdigest()
                for payload in provider_session.wire_pcm
            ),
            "wire_concat_sha256": hashlib.sha256(
                b"".join(provider_session.wire_pcm)
            ).hexdigest(),
            "wire_order": tuple(
                (
                    index,
                    hashlib.sha256(payload).hexdigest(),
                )
                for index, payload in enumerate(provider_session.wire_pcm, start=1)
            ),
            "wire_before_successor": provider_wire_before_successor,
            "wire_before_final": provider_wire_before_final,
            "wire_object_provenance": (
                provider_session.wire_pcm[0]
                is candidate_processed_frames[0].pcm16,
                provider_session.wire_pcm[0]
                is candidate_processed_frames[1].pcm16,
                provider_session.wire_pcm[1]
                is candidate_processed_frames[2].pcm16,
                provider_session.wire_pcm[2]
                is candidate_processed_frames[3].pcm16,
                provider_session.wire_pcm[3] is successor_processed_frame.pcm16,
            ),
            "submit_statuses": tuple(status.value for status in submit_statuses),
            "provider_callbacks": (
                ("endpoint", 1, endpoint_result),
                ("final", 1, final_result),
            ),
            "prepared_turns": tuple(
                turn_identity(token) for token in prepared_turns
            ),
            "finals": tuple(transcript_identity(event) for event in finals),
            "lifecycle": tuple(
                (event.state, event.provider, event.session_epoch)
                for event in lifecycle_notifications
            ),
            "snapshots": tuple(snapshots),
            "statuses": tuple(
                (event.code, event.provider, event.session_epoch)
                for event in statuses
            ),
            "failures": tuple(
                (event.code, event.provider, event.session_epoch)
                for event in failures
            ),
            "abandoned": tuple(
                turn_identity(token) for token in abandoned_turns
            ),
            "partials": tuple(
                (event.text, *turn_identity(event.turn_token)) for event in partials
            ),
            "provider_signal_end_count": provider_session.signal_end_count,
            "provider_connect_count": provider_session.connect_count,
            "provider_close_count": provider_session.close_count,
            "gate_pcm": tuple(gate.frames),
            "vad_load_count": vad.load_count,
            "vad_close_count": vad.close_count,
            "closed": (
                runtime._asr_session is None,
                runtime._asr_detector is None,
                runtime._asr_lifecycle is None,
                runtime._asr_warm_expiry_task is None,
            ),
        }

        assert detector_shadows == [shadow]
        assert factory_calls == (0 if shadow is None else 1)
        assert provider_wire_before_successor == (
            quiet + started,
            continued,
            paused,
        )
        assert provider_session.wire_pcm[1] is candidate_processed_frames[2].pcm16
        assert provider_session.wire_pcm[2] is candidate_processed_frames[3].pcm16
        assert provider_wire_before_final == provider_wire_before_successor
        if shadow is not None:
            assert shadow_submissions_before_successor == tuple(shadow.submissions[:3])
            assert shadow_submissions_before_final == shadow_submissions_before_successor
            assert len(shadow.submissions) == 4
            assert all(
                submission[0] is provider_payload
                for submission, provider_payload in zip(
                    shadow.submissions,
                    provider_session.wire_pcm,
                    strict=True,
                )
            )
            assert [submission[0] for submission in shadow.submissions] == [
                quiet + started,
                continued,
                paused,
                successor,
            ]
            assert quiet not in [
                submission[0] for submission in shadow.submissions
            ]
            assert all(submission[1] == 16_000 for submission in shadow.submissions)
            candidates = [submission[2] for submission in shadow.submissions]
            assert candidates[0] == candidates[1] == candidates[2]
            assert candidates[3] != candidates[0]
            assert all(
                getattr(candidate, "scope") == "provider_candidate"
                for candidate in candidates
            )
            assert getattr(candidates[3], "shadow_generation") > getattr(
                candidates[0],
                "shadow_generation",
            )
            assert shadow.finishes == [candidates[0]]
            assert shadow.reset_count == 0
            assert shadow.close_count == 1
        return trace, shadow

    disabled_a, _ = await replay(None)
    observed_false, false_shadow = await replay("false")
    observed_raising, raising_shadow = await replay("raises")
    disabled_b, _ = await replay(None)

    assert false_shadow is not None
    assert raising_shadow is not None
    assert disabled_a == observed_false == observed_raising == disabled_b
    assert disabled_a["wire_pcm"] == (
        quiet + started,
        continued,
        paused,
        successor,
    )
    assert disabled_a["wire_concat_sha256"] == hashlib.sha256(
        quiet + started + continued + paused + successor
    ).hexdigest()
    assert disabled_a["wire_object_provenance"] == (
        False,
        False,
        True,
        True,
        False,
    )
    assert disabled_a["submit_statuses"] == ("accepted",) * 5
    assert disabled_a["provider_callbacks"] == (
        ("endpoint", 1, None),
        ("final", 1, None),
    )
    assert disabled_a["provider_signal_end_count"] == 0
    assert disabled_a["provider_connect_count"] == 1
    assert disabled_a["provider_close_count"] == 1
    assert len(disabled_a["finals"]) == 1
